"""Celery application. Run workers and Beat as separate processes in production:

celery -A app.workers.celery_app worker -Q default,training --concurrency=2
celery -A app.workers.celery_app beat
"""

from __future__ import annotations

from celery import Celery, signals
from celery.schedules import crontab
from kombu import Queue

from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_var, task_var

s = get_settings()
celery_app = Celery("aurora", broker=s.broker_url, backend=s.result_backend, include=["app.workers.tasks"])
celery_app.conf.update(
    task_always_eager=s.celery_task_always_eager,
    task_eager_propagates=False,
    task_acks_late=True,  # a task is only acked after it finishes, so a crashed worker's task is redelivered
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_queues=(Queue("default"), Queue("training")),
    task_routes={
        "aurora.run_job": {"queue": "default"},
        "aurora.run_batch_forecast_job": {"queue": "default"},
        "aurora.run_training_job": {"queue": "training"},
    },
    task_soft_time_limit=s.training_time_limit_s + 60,
    task_time_limit=s.training_time_limit_s + 180,
    broker_transport_options={"visibility_timeout": s.training_time_limit_s + 600},
    broker_connection_retry_on_startup=True,
    result_expires=86400,
    worker_max_tasks_per_child=50,  # bound memory growth from PyTorch
    timezone="UTC",
    beat_schedule={
        "monitor-all-orgs": {"task": "aurora.monitor_all", "schedule": crontab(minute=0)},
        "dispatch-alerts": {"task": "aurora.poll_and_dispatch_alerts", "schedule": 300.0},
        "run-due-digests": {"task": "aurora.run_due_digests", "schedule": 300.0},
        "reap-stale-jobs": {"task": "aurora.reap_stale_jobs", "schedule": 600.0},
        "cleanup-audit": {"task": "aurora.cleanup_audit_logs", "schedule": crontab(hour=3, minute=15)},
        "purge-expired": {"task": "aurora.purge_expired", "schedule": crontab(hour=3, minute=30)},
        "nightly-batch-forecast": {"task": "aurora.batch_forecast_all", "schedule": crontab(hour=2, minute=0)},
    },
)


@signals.setup_logging.connect
def _logging(**_: object) -> None:
    """Workers and Beat log the same JSON lines as the API instead of Celery's own format."""
    configure_logging(s.log_level, s.log_json)


_task_tokens: dict[str, tuple] = {}


@signals.task_prerun.connect
def _task_started(task_id: str | None = None, task: object = None, kwargs: dict | None = None, **_: object) -> None:
    # Job tasks carry the id of the API request that started them, so their logs and audit entries correlate.
    # Tokens restore the previous values afterwards, which matters when a task runs inline (eager mode).
    _task_tokens[task_id or ""] = (
        task_var.set((task_id or "", getattr(task, "name", ""))),
        request_id_var.set((kwargs or {}).get("request_id") or request_id_var.get()),
    )


@signals.task_postrun.connect
def _task_finished(task_id: str | None = None, **_: object) -> None:
    tokens = _task_tokens.pop(task_id or "", None)
    if tokens is not None:
        task_var.reset(tokens[0])
        request_id_var.reset(tokens[1])
