"""Jobs/training, model registry, alerts, audit log and operational status."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Principal, require
from app.api.routes.health import check_components
from app.core import rate_limit
from app.core.errors import NotFound, ValidationFailed
from app.core.redis_client import get_redis
from app.core.security import Perm
from app.db.models import Alert, AuditEvent, Job, ModelVersion, User
from app.db.session import get_db
from app.schemas.common import Page
from app.services import alert_service, audit, audit_export, job_service, model_registry, onboarding_service

router = APIRouter(tags=["operations"])


# --- jobs ------------------------------------------------------------------------------------------


class TrainIn(BaseModel):
    horizon: Literal[7, 14, 28, 56] = 7
    max_epochs: int = Field(30, ge=1, le=200)
    auto_promote: bool = True


def job_out(j: Job) -> dict:
    return {
        "id": str(j.id),
        "kind": j.kind,
        "status": j.status,
        "progress": round(j.progress, 4),
        "params": j.params,
        "result": j.result,
        "error": j.error,
        "attempts": j.attempts,
        "cancel_requested": j.cancel_requested,
        "created_at": j.created_at.isoformat(),
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        "heartbeat_at": j.heartbeat_at.isoformat() if j.heartbeat_at else None,
    }


def _idem(key: str | None) -> str | None:
    if key is not None and not (1 <= len(key) <= 100):
        raise ValidationFailed("Idempotency-Key must be 1-100 characters")
    return key


@router.post("/train", status_code=202)
def train(
    body: TrainIn,
    idempotency_key: str | None = Header(None),
    p: Principal = Depends(require(Perm.train_models)),
    db: Session = Depends(get_db),
) -> dict:
    job, created = job_service.create(
        db,
        org_id=p.org_id,
        kind="train",
        params=body.model_dump(),
        user_id=p.user_id,
        idempotency_key=_idem(idempotency_key),
    )
    if created:
        audit.record_for(
            db, p, "model.train_requested", resource_type="job", resource_id=job.id, details=body.model_dump()
        )
        job_service.enqueue(db, job)
    db.refresh(job)
    return job_out(job)


@router.post("/jobs/batch-forecast", status_code=202)
def batch_forecast(
    horizon: int = Query(28, ge=7, le=90),
    idempotency_key: str | None = Header(None),
    p: Principal = Depends(require(Perm.train_models)),
    db: Session = Depends(get_db),
) -> dict:
    job, created = job_service.create(
        db,
        org_id=p.org_id,
        kind="batch_forecast",
        params={"horizon": horizon},
        user_id=p.user_id,
        idempotency_key=_idem(idempotency_key),
    )
    if created:
        job_service.enqueue(db, job)
    db.refresh(job)
    return job_out(job)


@router.post("/jobs/monitor", status_code=202)
def run_monitor(p: Principal = Depends(require(Perm.train_models)), db: Session = Depends(get_db)) -> dict:
    job, created = job_service.create(db, org_id=p.org_id, kind="monitor", params={}, user_id=p.user_id)
    if created:
        job_service.enqueue(db, job)
    db.refresh(job)
    return job_out(job)


@router.get("/jobs", response_model=Page[dict])
def list_jobs(
    kind: str | None = Query(None, max_length=30),
    status: str | None = Query(None, max_length=20),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require(Perm.read)),
    db: Session = Depends(get_db),
) -> Page[dict]:
    q = select(Job).where(Job.org_id == p.org_id)
    if kind:
        q = q.where(Job.kind == kind)
    if status:
        q = q.where(Job.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Job.created_at.desc()).limit(limit).offset(offset)).all()
    return Page(items=[job_out(j) for j in rows], total=total, limit=limit, offset=offset)


def _job(db: Session, org_id: uuid.UUID, job_id: uuid.UUID) -> Job:
    j = db.get(Job, job_id)
    if j is None or j.org_id != org_id:
        raise NotFound("Job not found")
    return j


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    return job_out(_job(db, p.org_id, job_id))


@router.get("/train/{job_id}/status")
def train_status(job_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    j = _job(db, p.org_id, job_id)
    out = job_out(j)
    res = j.result or {}
    hist = res.get("history") or []
    out.update(
        {
            "epoch": hist[-1]["epoch"] if hist else None,
            "loss": hist[-1]["train_loss"] if hist else None,
            "val_loss": hist[-1]["val_loss"] if hist else None,
            "phase": res.get("phase"),
        }
    )
    return out


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: uuid.UUID, p: Principal = Depends(require(Perm.train_models)), db: Session = Depends(get_db)
) -> dict:
    j = job_service.request_cancel(db, p.org_id, job_id)
    audit.record_for(db, p, "job.cancel", resource_type="job", resource_id=job_id)
    db.commit()
    return job_out(j)


# --- model registry ---------------------------------------------------------------------------------


def model_out(m: ModelVersion, full: bool = False) -> dict:
    metrics = m.metrics or {}
    out = {
        "id": str(m.id),
        "version": m.version,
        "kind": m.kind,
        "status": m.status,
        "gate_passed": m.gate_passed,
        "gate_reason": metrics.get("gate_reason"),
        "created_at": m.created_at.isoformat(),
        "promoted_at": m.promoted_at.isoformat() if m.promoted_at else None,
        "transformer_wape": metrics.get("transformer_wape"),
        "best_baseline": metrics.get("best_baseline"),
        "best_baseline_wape": metrics.get("best_baseline_wape"),
        "checksum": m.checksum,
        "products": len(m.training_meta.get("products", [])),
        "job_id": str(m.job_id) if m.job_id else None,
    }
    if full:
        out.update({"config": m.config, "metrics": metrics, "training": m.training_meta})
    return out


@router.get("/models/registry")
def registry(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(ModelVersion).where(ModelVersion.org_id == p.org_id).order_by(ModelVersion.created_at.desc()).limit(100)
    ).all()
    return {"items": [model_out(m) for m in rows]}


@router.get("/models/registry/{version_id}")
def registry_item(
    version_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> dict:
    return model_out(model_registry.get_version(db, p.org_id, version_id), full=True)


class PromoteIn(BaseModel):
    force: bool = False


@router.post("/models/registry/{version_id}/promote")
def promote(
    version_id: uuid.UUID,
    body: PromoteIn,
    p: Principal = Depends(require(Perm.manage_models)),
    db: Session = Depends(get_db),
) -> dict:
    mv = model_registry.promote(db, p.org_id, version_id, force=body.force)
    audit.record_for(
        db,
        p,
        "model.promote",
        resource_type="model_version",
        resource_id=mv.id,
        details={"version": mv.version, "force": body.force},
    )
    db.commit()
    return model_out(mv)


@router.post("/models/registry/rollback")
def rollback(p: Principal = Depends(require(Perm.manage_models)), db: Session = Depends(get_db)) -> dict:
    mv = model_registry.rollback(db, p.org_id)
    audit.record_for(
        db, p, "model.rollback", resource_type="model_version", resource_id=mv.id, details={"version": mv.version}
    )
    db.commit()
    return model_out(mv)


@router.get("/intelligence/diagnostics/{version_id}")
def diagnostics(version_id: str, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    if version_id == "active":
        mv = model_registry.get_active(db, p.org_id)
        if mv is None:
            raise NotFound("No active model")
    else:
        try:
            mv = model_registry.get_version(db, p.org_id, uuid.UUID(version_id))
        except ValueError as exc:
            raise ValidationFailed("version id must be a UUID or 'active'") from exc
    m = mv.metrics
    return {
        "model": model_out(mv),
        "feature_importance": m.get("importance"),
        "overall": m.get("overall"),
        "interval_coverage": (m.get("interval_coverage") or {}).get("nominal_vs_empirical"),
        "per_product": m.get("per_product"),
        "evaluation_segment": m.get("evaluation_segment"),
        "training": {
            k: mv.training_meta.get(k)
            for k in ("epochs_run", "history", "duration_s", "skipped_products", "n_train_windows", "n_cal_windows")
        },
    }


# --- alerts -----------------------------------------------------------------------------------------


def alert_out(a: Alert) -> dict:
    return {
        "id": str(a.id),
        "kind": a.kind,
        "severity": a.severity,
        "status": a.status,
        "title": a.title,
        "message": a.message,
        "product_id": str(a.product_id) if a.product_id else None,
        "details": a.details,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
        "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        "acknowledged_by": str(a.acknowledged_by) if a.acknowledged_by else None,
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
    }


@router.get("/alerts", response_model=Page[dict])
def list_alerts(
    status: Literal["open", "acknowledged", "resolved", "active"] | None = None,
    severity: Literal["low", "medium", "high", "critical"] | None = None,
    kind: str | None = Query(None, max_length=40),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require(Perm.read)),
    db: Session = Depends(get_db),
) -> Page[dict]:
    q = select(Alert).where(Alert.org_id == p.org_id)
    if status == "active":
        q = q.where(Alert.status != "resolved")
    elif status:
        q = q.where(Alert.status == status)
    if severity:
        q = q.where(Alert.severity == severity)
    if kind:
        q = q.where(Alert.kind == kind)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Alert.created_at.desc()).limit(limit).offset(offset)).all()
    return Page(items=[alert_out(a) for a in rows], total=total, limit=limit, offset=offset)


@router.get("/alerts/summary")
def alert_summary(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    rows = db.execute(
        select(Alert.severity, func.count())
        .where(Alert.org_id == p.org_id, Alert.status == "open")
        .group_by(Alert.severity)
    ).all()
    counts = {s: 0 for s in ("low", "medium", "high", "critical")}
    counts.update({s: n for s, n in rows})
    return {"open": sum(counts.values()), "by_severity": counts}


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge(
    alert_id: uuid.UUID, p: Principal = Depends(require(Perm.acknowledge_alerts)), db: Session = Depends(get_db)
) -> dict:
    a = alert_service.acknowledge(db, p.org_id, alert_id, p.user_id)
    audit.record_for(db, p, "alert.acknowledge", resource_type="alert", resource_id=alert_id)
    db.commit()
    return alert_out(a)


@router.post("/alerts/{alert_id}/resolve")
def resolve(
    alert_id: uuid.UUID, p: Principal = Depends(require(Perm.acknowledge_alerts)), db: Session = Depends(get_db)
) -> dict:
    a = alert_service.resolve(db, p.org_id, alert_id)
    audit.record_for(db, p, "alert.resolve", resource_type="alert", resource_id=alert_id)
    db.commit()
    return alert_out(a)


@router.delete("/alerts/{alert_id}", status_code=204)
def delete_alert(
    alert_id: uuid.UUID, p: Principal = Depends(require(Perm.delete_alerts)), db: Session = Depends(get_db)
) -> None:
    a = alert_service.get(db, p.org_id, alert_id)
    audit.record_for(db, p, "alert.delete", resource_type="alert", resource_id=alert_id, details={"title": a.title})
    db.delete(a)
    db.commit()


# --- audit & ops ------------------------------------------------------------------------------------


@router.get("/audit", response_model=Page[dict])
def audit_log(
    action: str | None = Query(None, max_length=80),
    actor: str | None = Query(None, max_length=320),
    outcome: Literal["success", "failure"] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require(Perm.view_audit)),
    db: Session = Depends(get_db),
) -> Page[dict]:
    f = audit_export.AuditFilter(action=action, actor=actor, outcome=outcome, since=since, until=until)
    total = audit_export.count(db, p.org_id, f)
    q = audit_export.query(p.org_id, f).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    rows = db.scalars(q.limit(limit).offset(offset)).all()
    return Page(items=[audit_export.event_out(e) for e in rows], total=total, limit=limit, offset=offset)


@router.get("/audit/export")
def audit_log_export(
    format: Literal["csv", "jsonl"] = "csv",
    action: str | None = Query(None, max_length=80),
    actor: str | None = Query(None, max_length=320),
    outcome: Literal["success", "failure"] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    p: Principal = Depends(require(Perm.view_audit)),
    db: Session = Depends(get_db),
) -> Response:
    """Download matching audit events, oldest first (ADR-039). The export is itself recorded in the audit log."""
    rate_limit.hit(f"audit-export:{p.org_id}", 10, 3600)
    f = audit_export.AuditFilter(action=action, actor=actor, outcome=outcome, since=since, until=until)
    body, n = audit_export.export(db, p.org_id, f, format)
    audit.record_for(
        db, p, "audit.export", resource_type="audit_log", details={"format": format, "rows": n, **f.as_dict()}
    )
    db.commit()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    media = "application/x-ndjson" if format == "jsonl" else "text/csv; charset=utf-8"
    return Response(
        body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="aurora-audit-{stamp}.{format}"'},
    )


@router.get("/onboarding")
def onboarding(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    """Getting-started checklist for the organisation (and the signed-in user's own security step)."""
    user = db.get(User, p.user_id)
    assert user is not None
    return onboarding_service.checklist(db, p.org_id, user)


@router.get("/ops/status")
def ops_status(p: Principal = Depends(require(Perm.view_ops)), db: Session = Depends(get_db)) -> dict:
    comps = check_components()
    queues = {}
    for qn in ("default", "training"):
        try:
            n = int(get_redis().llen(qn))
            queues[qn] = n
        except Exception:  # noqa: BLE001
            queues[qn] = None
    workers = None
    try:
        from app.workers.celery_app import celery_app

        if not celery_app.conf.task_always_eager:
            replies = celery_app.control.ping(timeout=1.0) or []
            workers = len(replies)
    except Exception:  # noqa: BLE001
        workers = None
    jobs = dict(
        db.execute(select(Job.status, func.count()).where(Job.org_id == p.org_id).group_by(Job.status)).tuples().all()
    )
    recent_failed = db.scalars(
        select(Job).where(Job.org_id == p.org_id, Job.status == "failed").order_by(Job.created_at.desc()).limit(5)
    ).all()
    return {
        "components": comps,
        "queues": queues,
        "workers_responding": workers,
        "jobs_by_status": jobs,
        "recent_failures": [
            {
                "id": str(j.id),
                "kind": j.kind,
                "error": j.error,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            }
            for j in recent_failed
        ],
    }
