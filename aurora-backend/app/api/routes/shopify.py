"""Shopify OAuth install and webhooks."""

from __future__ import annotations

import logging
import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import Principal, client_ip, require
from app.core import rate_limit
from app.core.config import get_settings
from app.core.errors import AppError, Unauthorized, ValidationFailed
from app.core.metrics import WEBHOOKS
from app.core.security import Perm
from app.db.session import get_db
from app.services import audit, shopify_service

router = APIRouter(prefix="/shopify", tags=["shopify"])
log = logging.getLogger(__name__)

MAX_WEBHOOK_BYTES = 1024 * 1024


class InstallIn(BaseModel):
    shop: str = Field(min_length=3, max_length=255)


@router.post("/install")
def install(
    body: InstallIn, p: Principal = Depends(require(Perm.manage_integrations)), db: Session = Depends(get_db)
) -> dict:
    url = shopify_service.start_install(db, p.org_id, p.user_id, body.shop)
    audit.record_for(
        db, p, "shopify.install_started", resource_type="shopify_shop", details={"shop": body.shop.lower()}
    )
    db.commit()
    return {"authorize_url": url}


@router.get("/callback", include_in_schema=False)
def callback(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """Browser redirect from Shopify. Never echoes internal errors; the UI shows a generic message."""
    target = get_settings().frontend_url.rstrip("/") + "/admin/ops"
    params = dict(request.query_params)
    try:
        rate_limit.hit(f"shopify-callback:{client_ip(request)}", 30, 60)
        shop = shopify_service.complete_install(db, params)
        audit.record(
            db,
            "shopify.installed",
            org_id=shop.org_id,
            resource_type="shopify_shop",
            resource_id=shop.id,
            ip=client_ip(request),
            details={"shop": shop.shop_domain, "scopes": shop.scopes},
        )
        db.commit()
        return RedirectResponse(f"{target}?{urlencode({'shopify': 'connected'})}", status_code=303)
    except AppError as exc:
        db.rollback()
        log.warning("shopify install failed", extra={"code": exc.code})
        return RedirectResponse(f"{target}?{urlencode({'shopify': 'error', 'reason': exc.code})}", status_code=303)


@router.get("/shops")
def shops(p: Principal = Depends(require(Perm.manage_integrations)), db: Session = Depends(get_db)) -> dict:
    return {"items": shopify_service.list_shops(db, p.org_id), "configured": bool(get_settings().shopify_api_key)}


@router.post("/shops/{shop_id}/webhooks")
def refresh_webhooks(
    shop_id: uuid.UUID, p: Principal = Depends(require(Perm.manage_integrations)), db: Session = Depends(get_db)
) -> dict:
    shop = shopify_service.refresh_webhooks(db, p.org_id, shop_id)
    audit.record_for(
        db,
        p,
        "shopify.webhooks_registered",
        resource_type="shopify_shop",
        resource_id=shop.id,
        details={"shop": shop.shop_domain, "webhooks": shop.webhooks},
    )
    db.commit()
    return {"id": str(shop.id), "webhooks": shop.webhooks}


@router.delete("/shops/{shop_id}", status_code=204)
def disconnect(
    shop_id: uuid.UUID, p: Principal = Depends(require(Perm.manage_integrations)), db: Session = Depends(get_db)
) -> None:
    shop = shopify_service.disconnect(db, p.org_id, shop_id)
    audit.record_for(
        db,
        p,
        "shopify.disconnected",
        resource_type="shopify_shop",
        resource_id=shop.id,
        details={"shop": shop.shop_domain},
    )
    db.commit()


async def _raw_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_WEBHOOK_BYTES:
        raise ValidationFailed("Webhook body too large")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_WEBHOOK_BYTES:
            raise ValidationFailed("Webhook body too large")
    return body


@router.post("/webhooks/{resource}/{event}")
def webhook(
    resource: str,
    event: str,
    request: Request,
    body: bytes = Depends(_raw_body),
    db: Session = Depends(get_db),
) -> dict:
    h = request.headers
    # Verify the signature before anything else: unauthenticated input gets no further than this line.
    if not shopify_service.verify_webhook_hmac(body, h.get("x-shopify-hmac-sha256")):
        WEBHOOKS.labels("shopify", "bad_signature").inc()
        log.warning("shopify webhook rejected: invalid signature")
        raise Unauthorized("Invalid webhook signature")
    topic = f"{resource}/{event}"
    if h.get("x-shopify-topic") and h.get("x-shopify-topic") != topic:
        raise ValidationFailed("Topic header does not match the endpoint")
    try:
        res = shopify_service.handle_webhook(
            db,
            topic=topic,
            shop_domain=h.get("x-shopify-shop-domain"),
            webhook_id=h.get("x-shopify-webhook-id") or h.get("x-shopify-event-id"),
            triggered_at=h.get("x-shopify-triggered-at"),
            body=body,
        )
        db.commit()
    except Exception:
        # Roll back the event row too, so Shopify's retry is processed rather than treated as a duplicate.
        db.rollback()
        WEBHOOKS.labels("shopify", "error").inc()
        raise
    WEBHOOKS.labels("shopify", "duplicate" if res.get("duplicate") else "processed").inc()
    return {"ok": True, **res}
