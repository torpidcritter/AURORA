# Implementation Status

_Last updated: 2026-10-27._ This is a record of what exists and how each part was checked. "Verified" means an
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

| Suite | Command | Result (2026-10-27) |
|---|---|---|
| Backend unit + integration + platform flow | `pytest -q` (PostgreSQL, fakeredis, eager Celery) | **398 passed** |
| Backend lint / migrations / deps | `ruff check`, `ruff format --check`, `alembic check`, `pip-audit` | clean (CI) |
| Frontend unit | `npm test` (Vitest) | **161 passed** |
| Frontend static | `npm run typecheck`, `npm run lint`, `npm run build`, `npm audit --audit-level=high` | clean |
| Browser E2E | `npx playwright test` against the built frontend, API, live Celery worker and Redis | 14 tests (CI; all 14 also passed locally on 2026-10-27) |
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
| Tenant isolation for products, jobs, alerts, users, settings, suppliers, purchase orders, overrides, the overstock report, locations and transfers, assistant tools and Shopify shops | integration tests in each area, and step 10 of the platform flow |
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
| Suppliers and purchase orders: unique names, version guards, supplier lead-time fallback, full lifecycle with stock movement, partial receipt, cancel releasing only outstanding stock, approval permission, concurrent sends applied once, distinct order numbers under concurrency, drafts from suggestions grouped by supplier and skipping busy products, CSV formula neutralising, audit | `tests/integration/test_purchasing.py`, `aurora-frontend/tests/purchasing.test.tsx`, Playwright purchasing flow |
| Forecast overrides: set/scale application, validation, no overlaps, archive, use in recommendations and next to the forecast, forecast value added against actuals; overstock and dead-stock report | `tests/integration/test_overrides_overstock.py`, `aurora-frontend/tests/purchasing.test.tsx` |
| Stock locations: legacy stock adopted into the default, levels always summing to the totals, counts and uploads requiring a location once there are several, transfers validated and never overselling under concurrency, location rules, purchase orders moving stock at their delivery location | `tests/integration/test_locations.py`, `aurora-frontend/tests/locations.test.tsx`, Playwright transfer flow |
| Category forecasts: categories with recent units, products adding up to the group for both reconciliation methods, recommendation following the lower backtest error, interval only on the group model, overrides flowing into the bottom-up total, stale and unsold products, group size cap, tenant isolation | `tests/integration/test_hierarchy.py`, `aurora-frontend/tests/hierarchy.test.tsx`, Playwright category forecast |
| Digests: slot timing, schedule rules and version guard, SSRF checks (scheme, credentials, port, private and reserved addresses, Slack host), encrypted targets never returned or audited, e-mail only with SMTP configured (fake SMTP server), scheduler sending each slot once, failed and redirected deliveries recorded, Slack escaping, content of every section, tenant isolation | `tests/integration/test_digests.py`, `aurora-frontend/tests/digests.test.tsx`, Playwright digest flow |
| Budget plan: expected shortage against simulation, order steps honouring pack size and MOQ, spend within the budget, the greedy policy never worse than funding by urgency, products without cost listed, category filter, drafts using the planned quantities, tenant isolation | `tests/integration/test_budget_plan.py`, `aurora-frontend/tests/budget.test.tsx`, Playwright budget flow |
| Supplier terms: price-break rules, version guard and audit, purchase order lines priced by tier (explicit prices kept), base-cost changes guarded, recommendation tier price and next-break hint, measured lead time, on-time and fill rate over the window, measured spread raising safety stock only with 3+ deliveries and yielding to an explicit override, tenant isolation | `tests/integration/test_supplier_terms.py`, `aurora-frontend/tests/pricing.test.tsx` |
| Audit export and retention: CSV and JSON Lines with filters and date range, own organisation only, formula cells neutralised, row cap, the export itself audited, retention per organisation with bounds, global default for others and system events, one purge note per organisation | `tests/integration/test_audit_export.py`, `aurora-frontend/tests/audit.test.ts`, Playwright audit export |
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
- Purchase orders are not e-mailed; "mark as sent" records that the user sent them (download the CSV). Receiving does not
  record lot, expiry or landed cost.
- Stock is kept per location, but forecasts and reorder suggestions are organisation-wide because sales carry no
  location.
- Category forecasts are computed on request (up to 200 products, about 30 seconds for a cold category in the E2E
  stack); there is no store level because sales carry no location.
- Digests are not retried automatically after a failed send, and e-mail has only been tested against a fake SMTP
  server. Webhook targets are checked against private networks, but DNS rebinding needs an egress firewall to rule out.
- The budget planner weighs every unit of demand equally (no selling prices or margins) and uses base unit costs.
- Measured supplier lead times use the time a receipt was recorded in Aurora, so late data entry looks like a late
  delivery.
- Overstock suggestions are descriptive only; there is no price or elasticity model.
- No penetration test or external security review has been done.

## Next steps
1. Run the assistant with a real API key and Shopify against a development store; review the audit output.
2. Deploy to a staging environment (Railway + Vercel or Compose behind TLS) and run the Locust test there to get
   capacity figures.
3. Reconcile Shopify order edits; add registry-side image scanning.
4. External security review before real customer data.
