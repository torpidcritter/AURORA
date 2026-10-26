"""Organisation settings: inventory defaults, monitoring thresholds and alert routing (per-org overrides)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import Principal, require
from app.core.security import Perm
from app.db.session import get_db
from app.schemas.settings import OrgSettings
from app.services import audit, settings_service

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/org")
def get_org_settings(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    """Effective value of every setting, the global default, and whether the organisation overrides it."""
    return settings_service.describe(db, p.org_id)


@router.patch("/org")
def update_org_settings(
    body: OrgSettings, p: Principal = Depends(require(Perm.manage_settings)), db: Session = Depends(get_db)
) -> dict:
    """Partial update: keys left out are unchanged, null clears an override (back to the global default)."""
    before, after = settings_service.update(db, p.org_id, body.model_dump(exclude_unset=True))
    if before != after:
        changed = [k for k in settings_service.KEYS if before.get(k) != after.get(k)]
        audit.record_for(
            db,
            p,
            "settings.updated",
            resource_type="organization",
            resource_id=p.org_id,
            details={"changed": changed, "before": before, "after": after},
        )
    db.commit()
    return settings_service.describe(db, p.org_id)
