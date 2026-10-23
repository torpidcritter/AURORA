# Implementation Status

_Last updated: 2026-10-23._ This is a record of what exists and how each part was checked. "Verified" means an
automated test or a recorded manual check passed in the environment below. It does not mean production-proven.

## Environment used for verification
- Linux sandbox, 4 CPUs, 15 GB RAM, Python 3.13, Node 22, PostgreSQL 16 (local cluster), Redis 7.
- **No Docker daemon.** The Dockerfiles and `docker-compose.yml` are written but no image was built or run here. CI
  does not build the images either.
- The PyTorch CPU index is blocked by the sandbox network policy, so the PyPI torch 2.14.1 wheel ran on CPU. CI and the
  backend Dockerfile use the CPU index.
- No Anthropic or Shopify credentials were available. Both integrations were tested against fakes; neither has been
  run against the real service.

## Automated tests

| Suite | Command | Result |
|---|---|---|
| Backend unit + integration | `pytest -q` (PostgreSQL `aurora_test`, fakeredis, eager Celery) | **175 passed** |
| Backend lint / migrations / deps | `ruff check`, `ruff format --check`, `alembic check`, `pip-audit` | clean |
| Frontend unit | `npm test` (Vitest) | **46 passed** |
| Frontend static | `npm run typecheck`, `npm run lint`, `npm run build`, `npm audit --audit-level=high` | clean |
| End-to-end | `npx playwright test` against built frontend + API + live Celery worker + Redis | **10 passed** (local) |
| CI | `.github/workflows/ci.yml` on PR #1 | see the PR checks |

How to run each one: `docs/TESTING.md`.

## Verified behaviour, by area

| Area | Evidence |
|---|---|
| Auth: login, no user enumeration, expired/forged/`alg=none` tokens, refresh rotation and reuse detection, lockout, rate limits, sessions invalidated on role or password change | `tests/integration/test_auth.py` |
| RBAC: every route × 4 roles + anonymous, and a check that no route is missing from the matrix | `test_rbac_matrix.py` |
| Tenant isolation for products, jobs, alerts, users, assistant tools and Shopify shops | integration tests in each area |
| Ingestion: file sniffing, size/row limits, zip-bomb guard, column mapping, validation, idempotent re-upload, quality score | `test_ingestion.py`, `test_datasets.py` |
| Inventory edits under concurrency (8 threads, exactly 1 wins) | `test_datasets.py` |
| Baselines, backtests without look-ahead, conformal intervals with near-nominal coverage on iid data | `test_ml_stats.py` |
| Transformer: architecture, leakage boundaries, save/load/predict, MC dropout is stochastic, cancellation | `test_transformer.py` |
| Inventory formulas against hand-computed values | `test_inventory_math.py` |
| Forecast, risk, KPI, recommendations, scenarios, signals, drift APIs | `test_forecasting_api.py` |
| Jobs, registry gate/promote/rollback, monitoring alerts with dedup/escalation/auto-resolve, audit, ops | `test_jobs_models_alerts.py` |
| Assistant: role-filtered tools, permission re-check, fallback on missing key/refusal/error, audit without the question text | `test_assistant.py` |
| Shopify: shop validation, single-use state, HMAC (query and body), freshness, idempotent webhooks, rollback on failure, encrypted tokens, key rotation, webhook metrics | `test_shopify.py` |
| Forecast evaluation computed once under concurrent requests | `test_eval_single_flight.py` |
| BFF: CSRF, path allow-list, blocked operational paths, cookie flags, refresh handling, right-most `X-Forwarded-For` | `aurora-frontend/tests/bff.test.ts` |
| Browser flows incl. `HttpOnly` cookies invisible to JS, CSRF block, a real training job on the worker, a viewer refused writes by the API | `aurora-frontend/e2e/platform.spec.ts` |

## Recorded manual checks
- **Backup/restore drill (2026-10-23):** `pg_dump -Fc` of the E2E database restored with `pg_restore` into a new
  database. All 18 tables had identical row counts and the `sales_records` content checksum matched. Model artifacts
  restored from a tarball matched the SHA-256 values in the registry. The Compose wrappers in `RUNBOOK.md` were not run.
- **Load test (2026-10-23):** 20 users for 89 s on one API process, 0 failures in 1,391 requests. Warm-cache medians:
  `/forecast` 130 ms, KPIs 100 ms, lists 11–15 ms. Cold-cache `/forecast` median was 7.5 s, down from 46 s before the
  single-flight cache. Details in `TESTING.md`. This is one small machine, not a capacity figure.

## Measured model behaviour (synthetic data, not a benchmark)
20 synthetic SKUs × 365 days, default config, CPU: training took 136 s. Held-out WAPE: Transformer 12.7, Holt-Winters
12.4, SMA 16.3, SES 16.7, seasonal-naive 16.8, Croston 16.3. The Transformer beat the best baseline on 8 of 20
products. Conformal coverage: 83% / 91.5% / 95% at nominal 80 / 90 / 95%. In the E2E run, a 3-epoch model (WAPE 16.4
vs Holt-Winters 12.5) was correctly kept as a candidate by the promotion gate.

## Implemented but not verified here
- Docker images and the Compose stack (no Docker daemon).
- Real Anthropic API calls (no key). Request shape follows the SDK documentation; the fallback path is tested.
- Real Shopify OAuth and webhooks (no app credentials).
- `S3Storage` against S3 or MinIO (only local storage is exercised).
- Alert webhook delivery to a real endpoint (tested with a fake HTTP client).

## Known limitations and risks
- Shopify order edits, cancellations and refunds are not reconciled. Shopify sales and uploaded sales for the same days
  are summed, so uploading both double-counts.
- Expired `oauth_states` and `refresh_tokens` rows are never purged.
- `TOKEN_ENCRYPTION_KEYS` rotation has no bulk re-encrypt command; tokens are re-encrypted when a shop reconnects.
- The first forecast per product after new data is slow (seconds) on CPU. Batch forecast jobs pre-warm the cache.
- Transformer training is CPU-bound (about 2 minutes for 20 SKUs × 1 year). Large catalogues need a GPU or sampling.
- `POST /inventory/upload` applies valid rows and reports invalid ones (row level, not all-or-nothing).
- Self-service password reset by email is not built (admins can reset passwords).
- Secure cookies mean the production web image only works over HTTPS or on `localhost`.

## Next steps
1. Build and run the Compose stack on a machine with Docker; add an image build job to CI.
2. Run the assistant with a real API key and Shopify against a development store.
3. Add a purge job for expired tokens/states and Shopify refund reconciliation.
