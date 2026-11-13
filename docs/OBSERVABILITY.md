# Observability

How to see what Aurora is doing in staging or production, and what to do when an alert fires (ADR-048).

## What the app gives you

| Signal | Where | Notes |
|---|---|---|
| Health | `GET /health/live` (process up), `GET /health/ready` (database, Redis, storage) | The container health check uses `/health/live`. |
| Metrics | `GET /metrics` (Prometheus text) | Requires `Authorization: Bearer $METRICS_TOKEN` when `METRICS_TOKEN` is set, which it should be outside development. Request metrics are aggregated across API worker processes; job, alert, queue and webhook figures are read from PostgreSQL and Redis at scrape time. |
| Logs | stdout, one JSON object per line | Every line from a request carries `request_id` (also returned as `X-Request-ID`); worker lines carry `task_id` and `task_name`. Sensitive keys are redacted. |
| Errors | Sentry or a Sentry-compatible service, when `SENTRY_DSN` is set | Off by default. Request bodies, cookies, query strings, most headers, e-mail and IP addresses and local variables are removed before sending; each report is tagged with `request_id` and `component` (`api` or `worker`). `SENTRY_TRACES_SAMPLE_RATE` (0 to 1, default 0) turns on performance traces. |
| Audit | Admin → Audit log, or the export | Who changed what. Not a monitoring signal, but the place to look after an incident. |

Set `APP_RELEASE` (for example the git commit) so errors and the `aurora_build_info` metric say which build is running.

## Metrics

| Metric | Type | Meaning |
|---|---|---|
| `aurora_http_requests_total{method,path,status}` | counter | Requests by route template (never raw paths or ids). |
| `aurora_http_request_duration_seconds{method,path}` | histogram | Request latency. |
| `aurora_webhooks_total{provider,outcome}` | counter | Inbound webhooks (Shopify). |
| `aurora_assistant_answers_total{mode}` | counter | Assistant answers from Claude or the built-in fallback. Many fallbacks mean Claude is unavailable or not configured. |
| `aurora_queue_depth{queue}` | gauge | Celery tasks waiting in Redis. |
| `aurora_jobs_in_progress{kind}` | gauge | Queued or running jobs. |
| `aurora_oldest_queued_job_age_seconds` | gauge | How long the oldest queued job has waited. |
| `aurora_jobs_heartbeat_stale` | gauge | Running jobs with no heartbeat for 10 minutes. |
| `aurora_forecast_jobs_total{kind,status}` | counter | Finished jobs by outcome. |
| `aurora_training_duration_seconds` | histogram | Successful training time. |
| `aurora_forecasts_total{method}` | counter | Stored forecasts by method. |
| `aurora_active_alerts_total` | gauge | Unresolved alerts across all organisations. |
| `aurora_webhook_deliveries{status}` | gauge | Stored outgoing deliveries (pending, succeeded, failed; kept 30 days). |
| `aurora_webhook_deliveries_overdue` | gauge | Pending deliveries more than 10 minutes past due. |
| `aurora_assistant_proposals_pending` | gauge | Assistant proposals waiting for a person. |
| `aurora_build_info{release,environment}` | gauge | Always 1; missing means `/metrics` could not read the database. |

Metrics are platform-wide and never carry an organisation, product or user, so they cannot leak tenant data to
whoever reads the dashboards.

## Setting up Prometheus, Grafana and alerting

`deploy/monitoring/` holds a ready configuration:

- `prometheus.yml`: scrapes the API with the metrics token read from a file, and loads the alert rules.
- `alerts.yml`: the alert rules below. `severity: page` means someone should look now; `ticket` means within a
  working day.
- `alertmanager.example.yml`: routes page alerts to an on-call receiver and the rest to e-mail. Fill in your own
  receivers; the repository keeps no addresses or keys.
- `grafana-dashboard.json`: import it in Grafana (Dashboards → Import) and choose your Prometheus data source.

A test (`tests/integration/test_observability.py`) checks that every metric the rules and the dashboard use is
really exported, so they cannot drift apart silently. Run `promtool check rules deploy/monitoring/alerts.yml` as
well if you have promtool.

An external uptime check (any service that requests `/health/ready` every minute from outside your network) is
still worth having: Prometheus cannot tell you that it is itself down.

## Alerts: what to check first

### AuroraApiDown
Prometheus cannot scrape the API. Check the API containers are running and healthy (`docker compose ps`, platform
dashboard), then `/health/ready` for which dependency fails. A wrong `METRICS_TOKEN` in Prometheus shows as a 401 in
the API logs.

### AuroraHighErrorRate
More than 2% of requests return 5xx. Look at the error reports (if Sentry is on) or the JSON logs filtered by
`"status": 5`, grouped by `path`. Take the `request_id` of one failure to see every log line for it. Recent deploys
and database or Redis trouble (`/health/ready`) are the usual causes.

### AuroraSlowRequests
Ordinary requests (training, forecasts, PDF reports and the assistant are excluded) are slow at the 95th percentile.
Check database load and connection counts, and whether a batch job is running at the same time
(`aurora_jobs_in_progress`). The dashboard's "Slowest routes" panel names the route.

### AuroraQueueBacklog
Tasks are piling up in Redis. Check that workers are running and not stuck (`celery inspect active` in a worker
container), and add worker replicas if the nightly batch is simply large.

### AuroraJobsNotStarting
A job has been queued for over 30 minutes. Usually no worker is consuming the queue (crashed, or started without
the `training` queue). Restart the worker; queued jobs are picked up again.

### AuroraJobsStuck
A running job stopped sending heartbeats. The worker may have been killed mid-task (out of memory during training is
the usual case). Check the worker logs for that `task_id`; the job can be cancelled where it was started (its
progress panel has a Cancel button, or `POST /jobs/{id}/cancel`) and started again.

### AuroraJobFailures
More than three jobs failed in an hour. Recent failures and their errors are listed under Admin → System &
integrations, and in the worker logs. Bad uploads
fail their own job only; many failures at once point at the database, storage or the model files.

### AuroraWebhooksNotSending
Outgoing webhook deliveries are past due, which means the `deliver-webhooks` beat entry is not running. Check that
exactly one Celery Beat process is up and that workers consume the default queue. Endpoints that fail on their
side do not trigger this; they show as failed deliveries on Admin → API and webhooks.

### AuroraStateMetricsMissing
`/metrics` answered but could not read PostgreSQL or Redis, so the job, queue and webhook figures are missing and the
alerts above cannot fire. Check `/health/ready` and the API logs for "could not compute state metrics".
