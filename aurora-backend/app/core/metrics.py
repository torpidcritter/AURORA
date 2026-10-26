"""Prometheus metrics. Labels use route templates, never raw paths or IDs, to bound cardinality.

Two sources feed /metrics:

1. Request-level counters recorded in the API process (HTTP traffic, inbound webhooks). The API runs several uvicorn
   worker processes, so when PROMETHEUS_MULTIPROC_DIR is set (the Docker image sets it for the API only) every process
   writes to shared files and a scrape aggregates all of them instead of reporting one random process.
2. Figures produced elsewhere, mostly in the Celery worker, which has no HTTP endpoint: job outcomes, forecasts
   generated, open alerts, training durations and queue depth. These are read from PostgreSQL and Redis at scrape
   time (cached briefly), so they are correct whichever process did the work.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest, multiprocess
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily

log = logging.getLogger(__name__)

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter("aurora_http_requests_total", "HTTP requests", ["method", "path", "status"], registry=REGISTRY)
HTTP_LATENCY = Histogram(
    "aurora_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=REGISTRY,
)
WEBHOOKS = Counter("aurora_webhooks_total", "Inbound webhooks", ["provider", "outcome"], registry=REGISTRY)

TRAINING_BUCKETS = (5, 15, 30, 60, 120, 300, 600, 1800)
QUEUES = ("default", "training")
DB_METRICS_TTL_S = 15.0


class StateCollector:
    """Exports metrics derived from the database and the broker at scrape time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached: list = []
        self._at = 0.0

    def describe(self) -> list:
        return []  # do not query the database at registration time

    def collect(self):  # type: ignore[no-untyped-def]
        with self._lock:
            if time.monotonic() - self._at > DB_METRICS_TTL_S:
                try:
                    self._cached = self._compute()
                except Exception as exc:  # noqa: BLE001 - a scrape must never fail because a dependency is down
                    log.warning("could not compute state metrics: %s", type(exc).__name__)
                    self._cached = []
                self._at = time.monotonic()
            yield from self._cached

    def _compute(self) -> list:
        from sqlalchemy import func, select

        from app.db.models import Alert, ForecastRun, Job
        from app.db.session import get_sessionmaker

        out: list = []
        db = get_sessionmaker()()
        try:
            jobs = CounterMetricFamily(
                "aurora_forecast_jobs", "Finished background jobs by kind and final status", labels=["kind", "status"]
            )
            for kind, status, n in db.execute(
                select(Job.kind, Job.status, func.count())
                .where(Job.status.in_(("succeeded", "failed", "cancelled")))
                .group_by(Job.kind, Job.status)
            ):
                jobs.add_metric([kind, status], n)
            out.append(jobs)

            running = GaugeMetricFamily("aurora_jobs_in_progress", "Queued or running jobs by kind", labels=["kind"])
            for kind, n in db.execute(
                select(Job.kind, func.count()).where(Job.status.in_(("queued", "running"))).group_by(Job.kind)
            ):
                running.add_metric([kind], n)
            out.append(running)

            forecasts = CounterMetricFamily("aurora_forecasts", "Stored forecasts by method", labels=["method"])
            for method, n in db.execute(select(ForecastRun.method, func.count()).group_by(ForecastRun.method)):
                forecasts.add_metric([method], n)
            out.append(forecasts)

            open_alerts = db.scalar(select(func.count()).select_from(Alert).where(Alert.status != "resolved")) or 0
            out.append(
                GaugeMetricFamily(
                    "aurora_active_alerts_total", "Open (unresolved) alerts across all organisations", value=open_alerts
                )
            )

            secs = func.extract("epoch", Job.finished_at - Job.started_at)
            cols = [func.count().filter(secs <= b) for b in TRAINING_BUCKETS] + [func.count(), func.sum(secs)]
            row = db.execute(
                select(*cols).where(Job.kind == "train", Job.status == "succeeded", Job.started_at.is_not(None))
            ).one()
            total, ssum = int(row[-2] or 0), float(row[-1] or 0.0)
            buckets = [(str(b), int(row[i] or 0)) for i, b in enumerate(TRAINING_BUCKETS)] + [("+Inf", total)]
            out.append(
                HistogramMetricFamily(
                    "aurora_training_duration_seconds",
                    "Successful model training duration",
                    buckets=buckets,
                    sum_value=ssum,
                )
            )
        finally:
            db.close()

        depth = GaugeMetricFamily("aurora_queue_depth", "Celery queue length", labels=["queue"])
        try:
            from app.core.redis_client import get_redis

            r = get_redis()
            for q in QUEUES:
                depth.add_metric([q], int(r.llen(q)))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read queue depth: %s", type(exc).__name__)
        out.append(depth)
        return out


STATE = StateCollector()
_STATE_REGISTRY = CollectorRegistry(auto_describe=False)
_STATE_REGISTRY.register(STATE)


def multiprocess_enabled() -> bool:
    return bool(os.environ.get("PROMETHEUS_MULTIPROC_DIR"))


def render() -> bytes:
    """Text exposition for /metrics: request metrics (aggregated across processes when enabled) + state metrics."""
    if multiprocess_enabled():
        reg = CollectorRegistry()
        multiprocess.MultiProcessCollector(reg)
        request_part = generate_latest(reg)
    else:
        request_part = generate_latest(REGISTRY)
    return request_part + generate_latest(_STATE_REGISTRY)
