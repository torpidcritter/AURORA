"""Durable job records. Status transitions are compare-and-set so duplicate deliveries do not double-run."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, Unavailable
from app.core.logging import request_id_var
from app.db.models import Job

logger = logging.getLogger("aurora.jobs")
MAX_ATTEMPTS = 3
SINGLE_ACTIVE = ("batch_forecast", "monitor")


def now() -> datetime:
    return datetime.now(UTC)


def create(
    db: Session,
    *,
    org_id: uuid.UUID,
    kind: str,
    params: dict,
    user_id: uuid.UUID | None,
    idempotency_key: str | None = None,
) -> tuple[Job, bool]:
    """Return (job, created). An existing job with the same idempotency key is returned unchanged."""
    if idempotency_key:
        existing = db.scalar(select(Job).where(Job.org_id == org_id, Job.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.kind != kind or existing.params != params:
                raise Conflict("Idempotency-Key was already used for a different request")
            return existing, False
    if kind in SINGLE_ACTIVE:
        # Backpressure: these jobs cover the whole catalogue, so a second copy would only repeat the work. The active
        # job is returned instead (beat ticks and repeated clicks while one is still running).
        active = db.scalar(
            select(Job)
            .where(Job.org_id == org_id, Job.kind == kind, Job.status.in_(("queued", "running")))
            .order_by(Job.created_at)
            .limit(1)
        )
        if active is not None:
            return active, False
    if kind == "train":
        running = db.scalar(
            select(Job.id).where(Job.org_id == org_id, Job.kind == "train", Job.status.in_(("queued", "running")))
        )
        if running is not None:
            raise Conflict(
                "A training job is already queued or running for this organisation", details={"job_id": str(running)}
            )
    job = Job(org_id=org_id, kind=kind, params=params, created_by=user_id, idempotency_key=idempotency_key)
    db.add(job)
    try:
        db.flush()
    except IntegrityError as exc:  # concurrent request: same idempotency key or a second training job
        db.rollback()
        if idempotency_key:
            existing = db.scalar(select(Job).where(Job.org_id == org_id, Job.idempotency_key == idempotency_key))
            if existing is not None:
                return existing, False
        if "uq_jobs_one_active_training" in str(exc.orig):
            raise Conflict("A training job is already queued or running for this organisation") from exc
        raise
    return job, True


def enqueue(db: Session, job: Job) -> None:
    """Commit the job, then hand it to the broker. If the broker is down the job is marked failed (not left
    'queued' forever) and the caller gets 503."""
    db.commit()
    try:
        res = send(job.kind, job.id)
    except Exception as exc:  # noqa: BLE001 - kombu raises many connection error types
        logger.error("could not enqueue job %s: %s", job.id, exc)
        db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "queued")
            .values(status="failed", error="Task queue unavailable; job was not started", finished_at=now())
        )
        db.commit()
        raise Unavailable("Task queue unavailable; the job was not started") from exc
    db.execute(update(Job).where(Job.id == job.id, Job.celery_task_id.is_(None)).values(celery_task_id=res.id))
    db.commit()
    db.refresh(job)


def send(kind: str, job_id: uuid.UUID | str, countdown: float | None = None):  # type: ignore[no-untyped-def]
    """Publish the task for a job (runs inline when CELERY_TASK_ALWAYS_EAGER is set, e.g. in tests)."""
    from app.workers import tasks

    task = {"train": tasks.run_training_job, "batch_forecast": tasks.run_batch_forecast_job}.get(kind, tasks.run_job)
    rid = request_id_var.get()
    return task.apply_async(
        args=[str(job_id)],
        kwargs={"request_id": rid} if rid else {},
        countdown=countdown,
        queue="training" if kind == "train" else "default",
    )


def claim(db: Session, job_id: uuid.UUID) -> Job | None:
    """queued -> running. Returns None if another worker already claimed it or it was cancelled."""
    row = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "queued", Job.cancel_requested.is_(False))
        .values(status="running", started_at=now(), heartbeat_at=now(), attempts=Job.attempts + 1)
        .returning(Job.id)
    ).first()
    db.commit()
    return db.get(Job, job_id) if row else None


def heartbeat(db: Session, job_id: uuid.UUID, progress: float | None = None, partial: dict | None = None) -> bool:
    """Update progress; returns True if cancellation was requested."""
    values: dict = {"heartbeat_at": now()}
    if progress is not None:
        values["progress"] = max(0.0, min(progress, 1.0))
    if partial is not None:
        values["result"] = partial
    db.execute(update(Job).where(Job.id == job_id, Job.status == "running").values(**values))
    db.commit()
    return bool(db.scalar(select(Job.cancel_requested).where(Job.id == job_id)))


def finish(
    db: Session, job_id: uuid.UUID, status: str, *, result: dict | None = None, error: str | None = None
) -> None:
    values: dict = {"status": status, "finished_at": now()}
    if status == "succeeded":
        values["progress"] = 1.0
    if result is not None:
        values["result"] = result
    if error is not None:
        values["error"] = error[:4000]
    db.execute(update(Job).where(Job.id == job_id, Job.status == "running").values(**values))
    db.commit()


def requeue_for_retry(db: Session, job_id: uuid.UUID, error: str) -> bool:
    job = db.get(Job, job_id)
    if job is None or job.attempts >= MAX_ATTEMPTS:
        return False
    db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "running")
        .values(status="queued", error=f"retrying after: {error[:500]}")
    )
    db.commit()
    return True


def request_cancel(db: Session, org_id: uuid.UUID, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.org_id != org_id:
        raise NotFound("Job not found")
    if job.status in ("succeeded", "failed", "cancelled"):
        raise Conflict(f"Job already {job.status}")
    if job.status == "queued":
        db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "queued")
            .values(status="cancelled", cancel_requested=True, finished_at=now())
        )
    db.execute(update(Job).where(Job.id == job_id).values(cancel_requested=True))
    db.commit()
    db.refresh(job)
    return job


def reap_stale(db: Session) -> int:
    """Jobs whose worker vanished (no heartbeat) are marked failed so they never hang in 'running'."""
    cutoff = now() - timedelta(minutes=get_settings().job_stale_after_minutes)
    rows = db.execute(
        update(Job)
        .where(Job.status == "running", Job.heartbeat_at < cutoff)
        .values(status="failed", finished_at=now(), error="Abandoned: no heartbeat from worker (worker lost?)")
        .returning(Job.id)
    ).all()
    db.commit()
    return len(rows)
