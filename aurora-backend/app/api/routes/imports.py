"""Scheduled data imports (ADR-050). Administrators only (`manage_integrations`): a source holds a credential for
another system, and its imports change the sales every forecast is built from."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Principal, require
from app.core import rate_limit
from app.core.security import Perm
from app.db.models import ImportSource
from app.db.session import get_db
from app.schemas.imports import ImportSourceIn, ImportSourceUpdate
from app.services import audit, import_service

router = APIRouter(tags=["imports"])
admin = require(Perm.manage_integrations)


def _audit(s: ImportSource) -> dict:
    # Never the address or credential: only what is safe to show.
    return {"name": s.name, "kind": s.kind, "host": s.url_hint, "frequency": s.frequency, "active": s.is_active}


def _payload(body: ImportSourceIn | ImportSourceUpdate, kind: str | None) -> dict:
    v = body.model_dump(exclude_unset=True, exclude={"credential", "options"})
    if body.credential is not None:
        v["credential"] = body.credential.model_dump()
    if body.options is not None:
        v["options"] = body.options.for_kind(kind or getattr(body, "kind", ""))
    return v


@router.get("/imports")
def list_sources(p: Principal = Depends(admin), db: Session = Depends(get_db)) -> list[dict]:
    return [import_service.source_out(s) for s in import_service.list_sources(db, p.org_id)]


@router.post("/imports", status_code=201)
def create_source(body: ImportSourceIn, p: Principal = Depends(admin), db: Session = Depends(get_db)) -> dict:
    s = import_service.create_source(db, p.org_id, p.user_id, _payload(body, body.kind))
    audit.record_for(db, p, "import.created", resource_type="import_source", resource_id=s.id, details=_audit(s))
    db.commit()
    return import_service.source_out(s)


@router.patch("/imports/{source_id}")
def update_source(
    source_id: uuid.UUID, body: ImportSourceUpdate, p: Principal = Depends(admin), db: Session = Depends(get_db)
) -> dict:
    kind = import_service.get_source(db, p.org_id, source_id).kind
    s = import_service.update_source(db, p.org_id, source_id, _payload(body, kind))
    details = {**_audit(s), "credential_changed": body.credential is not None, "address_changed": body.url is not None}
    audit.record_for(db, p, "import.updated", resource_type="import_source", resource_id=s.id, details=details)
    db.commit()
    return import_service.source_out(s)


@router.delete("/imports/{source_id}")
def delete_source(
    source_id: uuid.UUID,
    remove_sales: bool = Query(False, description="Also delete the sales this source imported"),
    p: Principal = Depends(admin),
    db: Session = Depends(get_db),
) -> dict:
    s = import_service.get_source(db, p.org_id, source_id)
    details = _audit(s)
    removed = import_service.delete_source(db, p.org_id, source_id, remove_sales=remove_sales)
    audit.record_for(
        db,
        p,
        "import.deleted",
        resource_type="import_source",
        resource_id=source_id,
        details={**details, "sales_rows_removed": removed},
    )
    db.commit()
    return {"deleted": True, "sales_rows_removed": removed}


@router.post("/imports/{source_id}/run")
def run_now(source_id: uuid.UUID, p: Principal = Depends(admin), db: Session = Depends(get_db)) -> dict:
    """Import now; the regular schedule is unchanged. Also how a new source's settings are tested."""
    rate_limit.hit(f"import-run:{p.org_id}", 20, 3600)
    s = import_service.get_source(db, p.org_id, source_id, lock=True)
    r = import_service.run(db, s, trigger="manual")
    audit.record_for(
        db,
        p,
        "import.run",
        resource_type="import_source",
        resource_id=s.id,
        outcome="success" if r.status != "failed" else "failure",
        details={"status": r.status, "rows": r.rows},
    )
    db.commit()
    return {"run": import_service.run_out(r), "source": import_service.source_out(s)}


@router.get("/imports/{source_id}/runs")
def list_runs(source_id: uuid.UUID, p: Principal = Depends(admin), db: Session = Depends(get_db)) -> list[dict]:
    return [import_service.run_out(r) for r in import_service.list_runs(db, p.org_id, source_id)]
