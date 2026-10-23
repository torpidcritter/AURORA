"""Prometheus metrics. Labels use route templates, never raw paths or IDs, to bound cardinality."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter("aurora_http_requests_total", "HTTP requests", ["method", "path", "status"], registry=REGISTRY)
HTTP_LATENCY = Histogram(
    "aurora_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=REGISTRY,
)
FORECAST_JOBS = Counter(
    "aurora_forecast_jobs_total",
    "Forecast/training jobs by kind and final status",
    ["kind", "status"],
    registry=REGISTRY,
)
FORECASTS_SERVED = Counter("aurora_forecasts_total", "Forecasts generated", ["method"], registry=REGISTRY)
ACTIVE_ALERTS = Gauge(
    "aurora_active_alerts_total", "Open (unresolved) alerts across all organisations", registry=REGISTRY
)
QUEUE_DEPTH = Gauge("aurora_queue_depth", "Celery queue length", ["queue"], registry=REGISTRY)
TRAINING_SECONDS = Histogram(
    "aurora_training_duration_seconds",
    "Model training duration",
    buckets=(5, 15, 30, 60, 120, 300, 600, 1800),
    registry=REGISTRY,
)
WEBHOOKS = Counter("aurora_webhooks_total", "Inbound webhooks", ["provider", "outcome"], registry=REGISTRY)
