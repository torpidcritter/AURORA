"""Data export and deletion: organisation exports, personal data, account erasure, organisation deletion (ADR-042)."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_principal, require
from app.api.routes.ops import job_out
from app.core import rate_limit
from app.core.security import Perm
from app.db.models import Job, User
from app.db.session import get_db
from app.services import audit, data_rights, job_service

router = APIRouter(tags=["data rights"])


class DeletionRequest(BaseModel):
    confirm_name: str = Field(min_length=1, max_length=200)
    current_password: str = Field(min_length=1, max_length=256)


def _user(db: Session, p: Principal) -> User:
    user = db.get(User, p.user_id)
    assert user is not None
    return user


@router.post("/data/exports", status_code=202)
def start_export(p: Principal = Depends(require(Perm.manage_settings)), db: Session = Depends(get_db)) -> dict:
    """Export every business table of the organisation as CSV files in a ZIP (built in the background)."""
    rate_limit.hit(f"data-export:{p.org_id}", 3, 3600)
    job, _ = job_service.create(db, org_id=p.org_id, kind="data_export", params={}, user_id=p.user_id)
    audit.record_for(db, p, "data.export_requested", resource_type="job", resource_id=job.id)
    job_service.enqueue(db, job)
    return job_out(job)


@router.get("/data/exports")
def list_exports(p: Principal = Depends(require(Perm.manage_settings)), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(Job).where(Job.org_id == p.org_id, Job.kind == "data_export").order_by(Job.created_at.desc()).limit(20)
    ).all()
    return [job_out(j) for j in rows]


@router.get("/data/exports/{job_id}/download")
def download_export(
    job_id: uuid.UUID, p: Principal = Depends(require(Perm.manage_settings)), db: Session = Depends(get_db)
) -> Response:
    data, filename = data_rights.export_download(db, p.org_id, job_id)
    audit.record_for(
        db, p, "data.export_downloaded", resource_type="job", resource_id=job_id, details={"bytes": len(data)}
    )
    db.commit()
    return Response(
        data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/auth/me/export")
def export_my_data(p: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> Response:
    """Everything Aurora holds about the signed-in person, as JSON."""
    rate_limit.hit(f"personal-export:{p.user_id}", 5, 3600)
    body = data_rights.personal_export(db, _user(db, p))
    audit.record_for(db, p, "data.personal_export", resource_type="user", resource_id=p.user_id)
    db.commit()
    return Response(
        json.dumps(body, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="aurora-my-data.json"'},
    )


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: uuid.UUID, p: Principal = Depends(require(Perm.manage_users)), db: Session = Depends(get_db)
) -> None:
    """Delete a person's account. Their name and e-mail are removed; records they made stay with the organisation."""
    former = data_rights.erase_user(db, _user(db, p), user_id)
    audit.record_for(db, p, "user.deleted", resource_type="user", resource_id=user_id, details={"email": former})
    db.commit()


@router.get("/organisation/deletion")
def deletion_status(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    return {"scheduled": data_rights.deletion_status(db, p.org_id)}


@router.post("/organisation/deletion")
def schedule_deletion(
    body: DeletionRequest, p: Principal = Depends(require(Perm.manage_settings)), db: Session = Depends(get_db)
) -> dict:
    """Schedule deletion of the whole organisation after a 7-day grace period, during which it can be cancelled."""
    info = data_rights.request_deletion(
        db, _user(db, p), confirm_name=body.confirm_name, password=body.current_password
    )
    audit.record_for(
        db, p, "organisation.deletion_scheduled", resource_type="organization", resource_id=p.org_id, details=info
    )
    db.commit()
    return {"scheduled": info}


@router.delete("/organisation/deletion", status_code=204)
def cancel_deletion(p: Principal = Depends(require(Perm.manage_settings)), db: Session = Depends(get_db)) -> None:
    data_rights.cancel_deletion(db, p.org_id)
    audit.record_for(db, p, "organisation.deletion_cancelled", resource_type="organization", resource_id=p.org_id)
    db.commit()
