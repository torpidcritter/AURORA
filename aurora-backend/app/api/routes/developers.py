"""API keys and outgoing webhooks (ADR-043). Administrators only (`manage_integrations`)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import Principal, require
from app.core import rate_limit
from app.core.security import Perm
from app.db.models import User
from app.db.session import get_db
from app.services import api_keys, audit, webhooks

router = APIRouter(tags=["api keys and webhooks"])
admin = require(Perm.manage_integrations)


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(default="viewer", max_length=20)
    expires_in_days: int | None = Field(default=90)


class EndpointCreate(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    events: list[str] = Field(min_length=1, max_length=20)
    description: str = Field(default="", max_length=200)


class EndpointUpdate(BaseModel):
    url: str | None = Field(default=None, min_length=8, max_length=2000)
    events: list[str] | None = Field(default=None, min_length=1, max_length=20)
    description: str | None = Field(default=None, max_length=200)
    active: bool | None = None


@router.get("/api-keys")
def list_keys(p: Principal = Depends(admin), db: Session = Depends(get_db)) -> list[dict]:
    return api_keys.list_for_org(db, p.org_id)


@router.post("/api-keys", status_code=201)
def create_key(body: KeyCreate, p: Principal = Depends(admin), db: Session = Depends(get_db)) -> dict:
    """Create a key. The full key is in this response only."""
    rate_limit.hit(f"api-key-create:{p.org_id}", 20, 3600)
    creator = db.get(User, p.user_id)
    assert creator is not None
    key, secret = api_keys.create(db, creator, name=body.name, role=body.role, expires_in_days=body.expires_in_days)
    audit.record_for(
        db,
        p,
        "api_key.created",
        resource_type="api_key",
        resource_id=key.id,
        details={
            "name": key.name,
            "role": key.role,
            "prefix": key.prefix,
            "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        },
    )
    db.commit()
    return {"key": api_keys.key_out(key, p.email), "secret": secret}


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_key(key_id: uuid.UUID, p: Principal = Depends(admin), db: Session = Depends(get_db)) -> None:
    key = api_keys.revoke(db, p.org_id, key_id)
    audit.record_for(
        db, p, "api_key.revoked", resource_type="api_key", resource_id=key.id, details={"prefix": key.prefix}
    )
    db.commit()


@router.get("/integrations/webhooks/events")
def list_events(p: Principal = Depends(admin)) -> list[dict]:
    return [{"key": k, "description": v} for k, v in webhooks.EVENTS.items()]


@router.get("/integrations/webhooks")
def list_endpoints(p: Principal = Depends(admin), db: Session = Depends(get_db)) -> list[dict]:
    return [webhooks.endpoint_out(e) for e in webhooks.list_endpoints(db, p.org_id)]


@router.post("/integrations/webhooks", status_code=201)
def create_endpoint(body: EndpointCreate, p: Principal = Depends(admin), db: Session = Depends(get_db)) -> dict:
    """Register an address. The signing secret is in this response only."""
    rate_limit.hit(f"webhook-create:{p.org_id}", 20, 3600)
    e, secret = webhooks.create_endpoint(
        db, p.org_id, p.user_id, url=body.url, events=body.events, description=body.description
    )
    audit.record_for(
        db,
        p,
        "webhook.created",
        resource_type="webhook_endpoint",
        resource_id=e.id,
        details={"host": e.url_hint, "events": e.events},
    )
    db.commit()
    return {"endpoint": webhooks.endpoint_out(e), "secret": secret}


@router.get("/integrations/webhooks/deliveries")
def list_deliveries(
    endpoint_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(admin),
    db: Session = Depends(get_db),
) -> dict:
    rows, total = webhooks.list_deliveries(db, p.org_id, endpoint_id=endpoint_id, limit=limit, offset=offset)
    return {"items": [webhooks.delivery_out(d) for d in rows], "total": total, "limit": limit, "offset": offset}


@router.post("/integrations/webhooks/deliveries/{delivery_id}/retry")
def retry_delivery(delivery_id: uuid.UUID, p: Principal = Depends(admin), db: Session = Depends(get_db)) -> dict:
    d = webhooks.retry(db, p.org_id, delivery_id)
    audit.record_for(db, p, "webhook.retry", resource_type="webhook_delivery", resource_id=d.id)
    db.commit()
    return webhooks.delivery_out(d)


@router.patch("/integrations/webhooks/{endpoint_id}")
def update_endpoint(
    endpoint_id: uuid.UUID, body: EndpointUpdate, p: Principal = Depends(admin), db: Session = Depends(get_db)
) -> dict:
    changes = body.model_dump(exclude_unset=True)
    e = webhooks.update_endpoint(db, p.org_id, endpoint_id, changes)
    details = {k: v for k, v in changes.items() if k != "url"} | ({"host": e.url_hint} if "url" in changes else {})
    audit.record_for(db, p, "webhook.updated", resource_type="webhook_endpoint", resource_id=e.id, details=details)
    db.commit()
    return webhooks.endpoint_out(e)


@router.delete("/integrations/webhooks/{endpoint_id}", status_code=204)
def delete_endpoint(endpoint_id: uuid.UUID, p: Principal = Depends(admin), db: Session = Depends(get_db)) -> None:
    webhooks.delete_endpoint(db, p.org_id, endpoint_id)
    audit.record_for(db, p, "webhook.deleted", resource_type="webhook_endpoint", resource_id=endpoint_id)
    db.commit()


@router.post("/integrations/webhooks/{endpoint_id}/rotate-secret")
def rotate_secret(endpoint_id: uuid.UUID, p: Principal = Depends(admin), db: Session = Depends(get_db)) -> dict:
    secret = webhooks.rotate_secret(db, p.org_id, endpoint_id)
    audit.record_for(db, p, "webhook.secret_rotated", resource_type="webhook_endpoint", resource_id=endpoint_id)
    db.commit()
    return {"secret": secret}


@router.post("/integrations/webhooks/{endpoint_id}/test")
def test_endpoint(endpoint_id: uuid.UUID, p: Principal = Depends(admin), db: Session = Depends(get_db)) -> dict:
    """Send a `ping` event now and report what the receiver answered."""
    rate_limit.hit(f"webhook-test:{p.org_id}", 30, 3600)
    d = webhooks.send_test(db, p.org_id, endpoint_id)
    db.commit()
    return webhooks.delivery_out(d)
