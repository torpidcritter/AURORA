# Implementation Status

_Last updated: 2026-10-26._ This is a record of what exists and how each part was checked. "Verified" means an
automated test, a CI job or a recorded manual check passed in the environments below. It does not mean
production-proven.

## Environments used for verification
- **Development sandbox:** Linux, 4 CPUs, Python 3.13, Node 22, PostgreSQL 16 (local cluster), Redis 7. No Docker
  daemon. The PyTorch CPU index is blocked there, so the PyPI torch 2.14.1 wheel ran on CPU.
- **GitHub Actions (PR #1):** backend, frontend, Playwright E2E, secret scan, and a containers job that builds the
  images, runs the Compose stack (with MinIO for S3 storage), bootstraps an admin, seeds synthetic data and runs the
  smoke test, a one-epoch training job on the worker container and a short Locust run.
- No Anthropic or Shopify credentials were available. Both integrations were tested against fakes and signed
  fixtures; neither has run against the real service.

## Automated tests

| Suite | Command | Result (2026-10-26) |
|---|---|---|
| Backend unit + integration + platform flow | `pytest -q` (PostgreSQL, fakeredis, eager Celery) | **319 passed** |
| Backend lint / migrations / deps | `ruff check`, `ruff format --check`, `alembic check`, `pip-audit` | clean (CI) |
| Frontend unit | `npm test` (Vitest) | **137 passed** |
| Frontend static | `npm run typecheck`, `npm run lint`, `npm run build`, `npm audit --audit-level=high` | clean |
| Browser E2E | `npx playwright test` against the built frontend, API, live Celery worker and Redis | 10 tests (CI) |
| Containers | CI `containers` job (images, Compose, restricted DB role, MinIO, smoke test, Locust smoke) | see PR checks |
| Secret scan | `python scripts/secret_scan.py` over tracked files (CI job) | no findings |

`mypy app` is configured but not enforced in CI; it reports 27 existing typing errors, none new in this round.
How to run each suite: `docs/TESTING.md`.

## Verified behaviour, by area

| Area | Evidence |
|---|---|
| Auth: login, no user enumeration, expired/forged/`alg=none` tokens, refresh rotation and reuse detection, lockout, per-email, per-IP and per-user rate limits, sessions invalidated on role or password change | `tests/integration/test_auth.py` |
| Admin-issued one-time reset links: hashed token, fragment-only URL, single use under concurrency, expiry, superseded by new links, deactivation or password change, own account refused, generic failure message, audit | `tests/integration/test_password_reset.py`, `aurora-frontend/tests/password-reset.test.tsx` |
| RBAC: every route × 4 roles + anonymous, and a check that no route is missing from the matrix | `test_rbac_matrix.py` |
| Tenant isolation for products, jobs, alerts, users, settings, assistant tools and Shopify shops | integration tests in each area, and step 10 of the platform flow |
| Per-organisation settings: strict validation, admin-only writes, row-locked updates, audit, effective values used by inventory, monitoring and alert routing | `tests/integration/test_org_settings.py`, `aurora-frontend/tests/settings.test.tsx` |
| Whole platform in one run: bootstrap, team, upload with quality report, training, registry, forecasts, recommendations, scenarios, alerts and acknowledgement, assistant fallback, audit trail, second tenant isolated | `tests/e2e/test_full_platform_flow.py` |
| Ingestion: sniffing, size/row limits, zip-bomb guard, column mapping, validation, idempotent re-upload, quality score, per-user upload limit | `test_ingestion.py`, `test_datasets.py`, `test_auth.py` |
| Baselines, backtests without look-ahead, conformal intervals with near-nominal coverage on iid data | `test_ml_stats.py` |
| Transformer: architecture, leakage boundaries, `.pt` bundle save/load/predict, MC dropout, cancellation | `test_transformer.py` |
| Inventory formulas against hand-computed values | `test_inventory_math.py` |
| Forecast, risk, KPI, recommendations, scenarios, signals, drift and live accuracy APIs | `test_forecasting_api.py`, `test_live_accuracy.py` |
| Jobs, single active catalogue job, registry gate/promote/rollback, monitoring alerts with dedup/escalation/auto-resolve, severity routing, worker logs with request and task ids | `test_jobs_models_alerts.py`, `unit/test_logging.py` |
| Restricted runtime DB role: DML only, no DDL, no UPDATE or TRUNCATE on audit entries, predeploy migrates then grants | `test_db_role.py`; CI containers job checks the running API |
| Storage: local and S3 (bucket creation, path-style endpoints) | `unit/test_storage.py`; CI writes datasets and model files to MinIO |
| Assistant: role-filtered tools, permission re-check, fallback on missing key/refusal/error, audit without the question text | `test_assistant.py` |
| Shopify: shop validation, single-use state, HMAC, freshness, idempotent webhooks, cancellations and refunds, GDPR redaction, failed-delivery audit, token re-encryption | `test_shopify.py` |
| BFF: CSRF, path allow-list, cookie flags, refresh handling, signed client address | `aurora-frontend/tests/bff.test.ts` |
| Assistant panel, upload wizard gating, model health and live accuracy views | `aurora-frontend/tests/{chat,wizard,health,live-accuracy}.test.tsx` |
| Load test logic: pacing under rate limits, pass/fail gates | `unit/test_load_support.py`; local and CI Locust runs |
| Secret scanner rules and allowlist | `unit/test_secret_scan.py` |

## Recorded manual checks
- **Backup/restore (2026-10-26):** `scripts/backup.sh` produced a custom-format dump and an artifacts archive; both
  restored into a fresh database and directory with matching row counts and checksums.
- **Restricted role, local stack (2026-10-26):** the API, worker and smoke test ran as `aurora_app` with S3 storage on
  a local S3 server; the training job stored its `.pt` file in the bucket.
- **Load test (2026-10-26):** the new `tests/load/locustfile.py`, 5 users for 30 s on one uvicorn process: 59
  requests, 0 failures, passed its gates; a deliberately impossible gate made it exit 1, missing credentials exit 2.
  This checks the script, not capacity. Earlier capacity notes (2026-10-23, older script) are in `TESTING.md`.

## Measured model behaviour (synthetic data, not a benchmark)
20 synthetic SKUs × 365 days, default config, CPU: training took 136 s. Held-out WAPE: Transformer 12.7, Holt-Winters
12.4, SMA 16.3, SES 16.7, seasonal-naive 16.8, Croston 16.3. The Transformer beat the best baseline on 8 of 20
products. Conformal coverage: 83% / 91.5% / 95% at nominal 80 / 90 / 95%. A weak model was correctly kept as a
candidate by the promotion gate.

## Implemented but not verified here
- Real Anthropic API calls (no key). Request shape follows the SDK documentation; the fallback path is tested.
- Real Shopify OAuth and webhooks (no app credentials).
- Railway and Vercel deployment (config written, never deployed).
- Alert webhook delivery to a real endpoint (tested with a fake HTTP client).
- AWS S3 itself (MinIO and a local S3 server were used).

## Known limitations and risks
- Shopify order edits (`orders/updated`) are not reconciled. Shopify and uploaded sales for the same days are summed.
- Password reset needs an admin to pass the link on; there is no e-mail provider (ADR-029).
- A sole admin who forgets their password has no recovery path short of direct database access; keep two admins.
- Container images are not scanned for OS-package CVEs in CI (ADR-026).
- The first forecast per product after new data takes seconds on CPU; batch forecast jobs pre-warm the cache.
- Transformer training is CPU-bound (about 2 minutes for 20 SKUs × 1 year). Large catalogues need a GPU or sampling.
- `POST /inventory/upload` applies valid rows and reports invalid ones (row level, not all-or-nothing).
- Secure cookies mean the production web image only works over HTTPS or on `localhost`.
- No penetration test or external security review has been done.

## Next steps
1. Run the assistant with a real API key and Shopify against a development store; review the audit output.
2. Deploy to a staging environment (Railway + Vercel or Compose behind TLS) and run the Locust test there to get
   capacity figures.
3. Reconcile Shopify order edits; add registry-side image scanning.
4. External security review before real customer data.
