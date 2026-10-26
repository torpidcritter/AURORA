"""Celery tasks. Each task is a thin wrapper around a service function operating on a durable Job row."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.db.models import AuditEvent, Organization, Product
from app.db.session import get_sessionmaker
from app.services import alert_service, job_service, maintenance, model_registry, monitoring_service, series
from app.services.forecast_service import ForecastRequest, generate
from app.workers.celery_app import celery_app

logger = logging.getLogger("aurora.tasks")
TRANSIENT = (OperationalError, ConnectionError, TimeoutError)


class _Cancelled(Exception):
    pass


def _run(job_id: str, handler) -> str:  # type: ignore[no-untyped-def]
    jid = uuid.UUID(job_id)
    db = get_sessionmaker()()
    try:
        job = job_service.claim(db, jid)
        if job is None:
            logger.info("job %s not claimable (duplicate delivery, cancelled or finished); skipping", jid)
            return "skipped"
        try:
            result = handler(db, job)
        except _Cancelled:
            db.rollback()
            from sqlalchemy import update

            from app.db.models import Job

            db.execute(update(Job).where(Job.id == jid).values(status="cancelled", finished_at=job_service.now()))
            db.commit()
            return "cancelled"
        except TRANSIENT as exc:
            db.rollback()
            if job_service.requeue_for_retry(db, jid, str(exc)):
                delay = 10 * 2 ** (job.attempts - 1)
                job_service.send(job.kind, job_id, countdown=delay)
                return "retrying"
            job_service.finish(db, jid, "failed", error=f"Transient error persisted after retries: {exc}")
            return "failed"
        except SoftTimeLimitExceeded:
            db.rollback()
            job_service.finish(db, jid, "failed", error="Task exceeded its time limit")
            return "failed"
        except Exception as exc:  # noqa: BLE001 - recorded on the job for diagnosis
            db.rollback()
            logger.exception("job %s failed", jid)
            job_service.finish(db, jid, "failed", error=f"{type(exc).__name__}: {exc}")
            return "failed"
        job_service.finish(db, jid, "succeeded", result=result)
        return "succeeded"
    finally:
        db.close()


def _train(db, job) -> dict:  # type: ignore[no-untyped-def]
    from app.ml.training import TrainConfig, TrainingCancelled, train

    s = get_settings()
    p = job.params
    cfg = TrainConfig(
        window=s.model_window,
        horizon=int(p.get("horizon", s.model_horizon)),
        d_model=s.model_d_model,
        nhead=s.model_heads,
        num_layers=s.model_layers,
        dropout=s.model_dropout,
        max_epochs=int(p.get("max_epochs", s.training_max_epochs)),
        time_limit_s=s.training_time_limit_s,
    )
    skus = dict(db.execute(select(Product.id, Product.sku).where(Product.org_id == job.org_id)).tuples().all())
    data = {skus[pid]: y for pid, y in series.load_many(db, job.org_id).items() if pid in skus}
    history: list[dict] = []

    def progress(epoch: int, max_epochs: int, tl: float, vl: float) -> None:
        history.append({"epoch": epoch, "train_loss": round(tl, 5), "val_loss": round(vl, 5)})
        if job_service.heartbeat(
            db,
            job.id,
            0.05 + 0.85 * epoch / max_epochs,
            {"phase": "training", "epoch": epoch, "max_epochs": max_epochs, "history": history},
        ):
            raise _Cancelled()

    def cancelled() -> bool:
        return job_service.heartbeat(db, job.id)

    job_service.heartbeat(db, job.id, 0.02, {"phase": "loading data", "products": len(data)})
    try:
        bundle = train(data, cfg, progress=progress, should_cancel=cancelled)
    except TrainingCancelled as exc:
        raise _Cancelled() from exc
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    job_service.heartbeat(db, job.id, 0.95, {"phase": "registering model", "history": history})
    mv = model_registry.register(db, org_id=job.org_id, bundle=bundle, job_id=job.id, created_by=job.created_by)
    promoted = False
    if p.get("auto_promote", True) and mv.gate_passed:
        model_registry.promote(db, job.org_id, mv.id)
        promoted = True
    db.commit()
    return {
        "model_version_id": str(mv.id),
        "version": mv.version,
        "gate_passed": mv.gate_passed,
        "gate_reason": mv.metrics.get("gate_reason"),
        "promoted": promoted,
        "transformer_wape": bundle["metrics"]["transformer_wape"],
        "best_baseline": bundle["metrics"]["best_baseline"],
        "best_baseline_wape": bundle["metrics"]["best_baseline_wape"],
        "epochs_run": bundle["training"]["epochs_run"],
        "skipped_products": bundle["training"]["skipped_products"],
        "history": history,
    }


def _batch_forecast(db, job) -> dict:  # type: ignore[no-untyped-def]
    horizon = int(job.params.get("horizon", 28))
    pids = db.scalars(select(Product.id).where(Product.org_id == job.org_id).order_by(Product.sku)).all()
    done, failed = 0, []
    for i, pid in enumerate(pids):
        try:
            generate(db, job.org_id, ForecastRequest(product_id=pid, horizon=horizon, mc_samples=0))
            db.commit()
            done += 1
        except Exception as exc:  # noqa: BLE001 - per-product failures are recorded, not fatal
            db.rollback()
            failed.append({"product_id": str(pid), "error": str(exc)[:200]})
        if job_service.heartbeat(db, job.id, (i + 1) / max(len(pids), 1)):
            raise _Cancelled()
    return {"forecasts": done, "failed": failed, "horizon": horizon}


def _monitor(db, job) -> dict:  # type: ignore[no-untyped-def]
    return monitoring_service.run_for_org(db, job.org_id)


HANDLERS = {"train": _train, "batch_forecast": _batch_forecast, "monitor": _monitor}


@celery_app.task(name="aurora.run_training_job", acks_late=True)
def run_training_job(job_id: str, request_id: str | None = None) -> str:
    return _run(job_id, _train)


@celery_app.task(name="aurora.run_batch_forecast_job", acks_late=True)
def run_batch_forecast_job(job_id: str, request_id: str | None = None) -> str:
    """Forecast every product in the organisation's catalogue (spec 6.1)."""
    return _run(job_id, _batch_forecast)


@celery_app.task(name="aurora.run_job", acks_late=True)
def run_job(job_id: str, request_id: str | None = None) -> str:
    db = get_sessionmaker()()
    try:
        from app.db.models import Job

        job = db.get(Job, uuid.UUID(job_id))
        kind = job.kind if job else None
    finally:
        db.close()
    if kind not in HANDLERS:
        return "unknown"
    return _run(job_id, HANDLERS[kind])


def _for_each_org(kind: str, params: dict | None = None) -> int:
    db = get_sessionmaker()()
    n = 0
    try:
        hour = datetime.now(UTC).strftime("%Y%m%d%H")
        for org_id in db.scalars(select(Organization.id)).all():
            # idempotency key per org per hour: a re-fired Beat tick does not create duplicate jobs
            job, created = job_service.create(
                db, org_id=org_id, kind=kind, params=params or {}, user_id=None, idempotency_key=f"beat:{kind}:{hour}"
            )
            if created:
                job_service.enqueue(db, job)
                n += 1
            else:
                db.commit()
    finally:
        db.close()
    return n


@celery_app.task(name="aurora.monitor_all")
def monitor_all() -> int:
    return _for_each_org("monitor")


@celery_app.task(name="aurora.batch_forecast_all")
def batch_forecast_all() -> int:
    return _for_each_org("batch_forecast", {"horizon": 28})


@celery_app.task(name="aurora.poll_and_dispatch_alerts", autoretry_for=TRANSIENT, retry_backoff=True, max_retries=3)
def poll_and_dispatch_alerts() -> dict:
    db = get_sessionmaker()()
    try:
        return alert_service.dispatch(db)
    finally:
        db.close()


@celery_app.task(name="aurora.cleanup_audit_logs")
def cleanup_audit_logs() -> int:
    days = get_settings().audit_retention_days
    db = get_sessionmaker()()
    try:
        res = db.execute(delete(AuditEvent).where(AuditEvent.created_at < datetime.now(UTC) - timedelta(days=days)))
        db.commit()
        return int(res.rowcount or 0)  # type: ignore[attr-defined]
    finally:
        db.close()


@celery_app.task(name="aurora.purge_expired")
def purge_expired() -> dict:
    db = get_sessionmaker()()
    try:
        counts = maintenance.purge_expired(db)
        logger.info("purged expired rows", extra={"counts": counts})
        return counts
    finally:
        db.close()


@celery_app.task(name="aurora.reap_stale_jobs")
def reap_stale_jobs() -> int:
    db = get_sessionmaker()()
    try:
        return job_service.reap_stale(db)
    finally:
        db.close()
