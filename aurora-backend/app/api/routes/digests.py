"""Scheduled digests (ADR-036): schedules are admin-managed; every role can read the digests sent in-app."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Principal, require
from app.core import rate_limit
from app.core.security import Perm
from app.db.session import get_db
from app.schemas.digests import DigestPreviewIn, DigestScheduleIn, DigestScheduleUpdate
from app.services import audit, digest_service

router = APIRouter(tags=["digests"])


@router.get("/digests/schedules")
def list_schedules(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> list[dict]:
    return [digest_service.schedule_out(s) for s in digest_service.list_schedules(db, p.org_id)]


@router.post("/digests/schedules", status_code=201)
def create_schedule(
    body: DigestScheduleIn, p: Principal = Depends(require(Perm.manage_settings)), db: Session = Depends(get_db)
) -> dict:
    s = digest_service.create_schedule(db, p.org_id, p.user_id, body.model_dump())
    audit.record_for(
        db, p, "digest.schedule_created", resource_type="digest_schedule", resource_id=str(s.id), details=_audit(s)
    )
    db.commit()
    return digest_service.schedule_out(s)


@router.patch("/digests/schedules/{schedule_id}")
def update_schedule(
    schedule_id: uuid.UUID,
    body: DigestScheduleUpdate,
    p: Principal = Depends(require(Perm.manage_settings)),
    db: Session = Depends(get_db),
) -> dict:
    s = digest_service.update_schedule(db, p.org_id, schedule_id, body.model_dump(exclude_unset=True))
    audit.record_for(
        db, p, "digest.schedule_updated", resource_type="digest_schedule", resource_id=str(s.id), details=_audit(s)
    )
    db.commit()
    return digest_service.schedule_out(s)


@router.delete("/digests/schedules/{schedule_id}", status_code=204)
def delete_schedule(
    schedule_id: uuid.UUID, p: Principal = Depends(require(Perm.manage_settings)), db: Session = Depends(get_db)
) -> None:
    digest_service.delete_schedule(db, p.org_id, schedule_id)
    audit.record_for(
        db, p, "digest.schedule_deleted", resource_type="digest_schedule", resource_id=str(schedule_id), details={}
    )
    db.commit()


@router.post("/digests/schedules/{schedule_id}/send")
def send_now(
    schedule_id: uuid.UUID, p: Principal = Depends(require(Perm.manage_settings)), db: Session = Depends(get_db)
) -> dict:
    """Build and send this digest now (its regular slot is unchanged)."""
    rate_limit.hit(f"digest-send:{p.org_id}", 10, 3600)
    s = digest_service.get_schedule(db, p.org_id, schedule_id)
    r = digest_service.run(db, p.org_id, s, trigger="manual")
    audit.record_for(
        db,
        p,
        "digest.sent",
        resource_type="digest_run",
        resource_id=str(r.id),
        details={"status": r.status, "schedule_id": str(s.id)},
    )
    db.commit()
    return digest_service.run_out(r, with_content=True)


@router.post("/digests/preview")
def preview(body: DigestPreviewIn, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    rate_limit.hit(f"digest-preview:{p.user_id}", 10, 60)
    content = digest_service.build(db, p.org_id, list(body.sections), body.period_days)
    db.commit()  # forecasts generated for the stockout section are stored, as for recommendations
    return {"content": content, "text": digest_service.render_lines("Digest preview", content)}


@router.get("/digests")
def list_digests(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require(Perm.read)),
    db: Session = Depends(get_db),
) -> dict:
    rows, total = digest_service.list_runs(db, p.org_id, limit, offset)
    return {"items": [digest_service.run_out(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/digests/{digest_id}")
def get_digest(digest_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    return digest_service.run_out(digest_service.get_run(db, p.org_id, digest_id), with_content=True)


def _audit(s) -> dict:  # type: ignore[no-untyped-def]
    # The target itself is a secret (a webhook URL is a credential); only its masked hint is recorded.
    return {
        "name": s.name,
        "frequency": s.frequency,
        "weekday": s.weekday,
        "hour_utc": s.hour_utc,
        "sections": list(s.sections),
        "channel": s.channel,
        "target_hint": s.target_hint,
        "is_active": s.is_active,
    }
