# Implementation Status

_Last updated: 2026-10-23 (session 1)._ Honest record of what exists and how it was verified.

## Environment used for verification
- Linux sandbox, 4 CPUs, 15 GB RAM, Python 3.13.17, Node 22.22, PostgreSQL 16 (local cluster), Redis 7.0.15.
- **No Docker daemon** is available, so the Docker Compose stack is written but not run here.
- Network policy blocks `download.pytorch.org`. The PyPI torch 2.14.1 wheel (CUDA build, running on CPU) was used.

## Verified (automated tests passing)
Command: `cd aurora-backend && pytest tests` → **145 passed** against PostgreSQL `aurora_test` + fakeredis, Celery in
eager mode. Command: `ruff check app tests` → clean.

| Area | Evidence (test module) |
|---|---|
| Auth: login, bad creds, no user enumeration, expired/forged/`alg=none` tokens, refresh rotation + reuse detection, logout, lockout, rate limit, role change and password change invalidate sessions, tenant-scoped user admin | `tests/integration/test_auth.py` |
| RBAC matrix: every protected route × 4 roles + unauthenticated; asserts no route is missing from the matrix | `tests/integration/test_rbac_matrix.py` |
| Health/metrics/request IDs/error envelope | `tests/integration/test_health.py` |
| Ingestion rules, malformed files, quality score formula | `tests/unit/test_ingestion.py`, `tests/integration/test_datasets.py` |
| Concurrent inventory edits (8 threads, exactly 1 wins), optimistic concurrency, tenant isolation | `tests/integration/test_datasets.py` |
| Metrics with zero actuals, baselines, no-lookahead backtests, conformal finite-sample quantile and ~nominal coverage on iid data | `tests/unit/test_ml_stats.py` |
| Transformer shapes, spec architecture, leakage boundaries, NaN rejection, train→serialize→load→predict, MC dropout really stochastic, cancellation | `tests/unit/test_transformer.py` |
| Inventory formulas against hand-computed values, √L scaling, edge cases, invalid inputs | `tests/unit/test_inventory_math.py` |
| Forecast API, intervals, validation, explain, inventory/risk/KPI/recommendations, backtest API, scenarios, signals, drift | `tests/integration/test_forecasting_api.py` |
| Training job E2E (eager), idempotency key, registry promote/rollback, Transformer inference from stored artifact, single active training job, duplicate delivery skip, cancel, stale reaper, broker-down → 503, batch forecast, monitoring alerts dedup/escalation/ack/auto-resolve, idempotent delivery, dead-lettering, audit API, ops status, cross-tenant job/alert access | `tests/integration/test_jobs_models_alerts.py` |

## Measured model behaviour (not a benchmark; synthetic data)
20 synthetic SKUs × 365 days, default config, CPU: training took 136 s (15 epochs, early stopped). Held-out WAPE:
Transformer 12.7, Holt-Winters 12.4, SMA 16.3, SES 16.7, seasonal-naive 16.8, Croston 16.3. The Transformer beat the best
baseline on 8 of 20 products. Empirical conformal coverage: 83% / 91.5% / 95% at nominal 80 / 90 / 95%.

## Implemented but not yet verified
- Live Celery worker + Beat against real Redis (only eager mode is tested so far).
- S3/MinIO storage backend (`S3Storage`). Only local storage is exercised.
- Webhook alert channel against a real endpoint (tested with a fake HTTP client).

## Not implemented yet
Frontend, Claude assistant, Shopify integration, Dockerfiles/compose, CI, load test, backup/restore drill.

## Known limitations / risks
- `POST /inventory/upload` applies valid rows and reports invalid ones (row-level, not all-or-nothing).
- Risk matrix and KPIs compute forecasts on demand (cached per data fingerprint). The first call on a large catalogue is slow.
  Batch forecast jobs pre-warm the cache.
- Transformer training is CPU-bound: about 2 minutes for 20 SKUs × 1 year. Large catalogues need a GPU or sampling.

## Next exact step
Build `aurora-frontend/` (Next.js 14, BFF auth), then the assistant and Shopify integration, then Docker/CI.
