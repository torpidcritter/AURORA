# Implementation Plan

Prioritised backlog. `[x]` = implemented **and** covered by passing automated tests; `[~]` = implemented, partially verified;
`[ ]` = not started. Current evidence lives in `IMPLEMENTATION_STATUS.md`.

## P0 Foundation
- [x] Repository layout, pinned backend dependencies, `.env.example`, lint/format config
- [x] Centralised settings with production guards (refuses default secret, wildcard CORS)
- [x] PostgreSQL schema + Alembic migration (FKs, unique/partial-unique indexes, check constraints, UTC)
- [x] Structured JSON logs, request IDs, consistent error envelope, security headers
- [x] Health: liveness, readiness (DB/Redis/storage), public summary; Prometheus `/metrics` with route-template labels

## P1 Identity & access
- [x] Login, logout, refresh rotation with reuse detection, lockout, Redis rate limits
- [x] RBAC (viewer < operator < analyst < admin) enforced server-side; full route permission matrix test
- [x] Tenant (organisation) isolation on every object; cross-tenant tests
- [x] User admin (create, role change, deactivate, password reset) with session invalidation
- [x] Audit log with redaction; audit query API
- [x] Admin-issued one-time password reset links (ADR-029)
- [ ] Self-service "forgot password" by e-mail (needs an e-mail provider)
- [x] Per-organisation settings: inventory defaults, monitoring thresholds, alert routing (ADR-030)
- [x] Restricted runtime database role; migrations run as the schema owner (ADR-019)

## P2 Data
- [x] CSV/XLSX/JSON upload, content-type sniffing, size/row limits, zip-bomb guard
- [x] Fuzzy column mapping, row validation with error samples, duplicate merge, gap accounting, quality score
- [x] Idempotent re-upload (sha256), overlapping-date replacement, missing-date policy (zero / interpolate)
- [x] Products catalog (search/filter/sort/paginate), history API, inventory edits with optimistic concurrency, snapshot upload

## P3 Forecasting
- [x] SMA, SES, seasonal-naive, Holt-Winters (damped, weekly), Croston-SBA
- [x] Rolling-origin backtests (K=4), MAE/RMSE/WAPE/MAPE(zero-excluded)/bias, per-horizon MAE
- [x] Split-conformal intervals per horizon step with measured coverage
- [x] Transformer (spec architecture + product embedding), leakage-safe splits, early stopping, artifact save/load
- [x] MC dropout (fast path disabled, verified stochastic), permutation importance
- [x] Auto method selection per product; Transformer promotion gate vs baselines
- [x] Explanations grounded in data (no SHAP claims, no invented causes)

## P4 Inventory intelligence
- [x] Safety stock (√L, optional lead-time variability), ROP, order-up-to ROQ with pack/MOQ
- [x] Days-until-stockout, stockout probability, urgency score + levels
- [x] Risk matrix, KPI rollup with documented definitions, prescriptive recommendations
- [x] What-if scenarios (8 types, bounded params), external signals CRUD/CSV

## P5 Jobs, registry, monitoring
- [x] Durable jobs, atomic claim, retries with backoff, cancellation, stale-job reaper, broker-down handling
- [x] Celery worker/Beat config (acks_late, prefetch 1, time limits, separate queues)
- [x] Model registry: semver versions, single active pointer, gated promotion, rollback, checksum-verified artifacts
- [x] Monitoring: stockout/drift/staleness/accuracy alerts, dedup + escalation + auto-resolve, idempotent delivery
- [x] Real Celery worker + Redis broker run (Playwright E2E trains a model through a live worker)

## P6 Frontend
- [x] Next.js 16 app shell, BFF auth (httpOnly cookies, CSRF), typed API client (ADR-014)
- [x] Pages: login, dashboard, upload wizard, products, product detail, forecasts, inventory, risk matrix, scenarios,
      signals, alerts, models/drift with live accuracy, assistant (page and floating panel), users with reset links,
      organisation settings, password reset, audit, system & integrations
- [x] Unit tests (Vitest + Testing Library) and Playwright E2E against the real backend and worker

## P7 Integrations
- [~] Claude assistant with authorised, validated tools + deterministic fallback (tested with a fake client; no real API key used)
- [~] Shopify OAuth and signed, idempotent webhooks; order → sales mapping (tested with signed fixtures; no real store used)
- [x] Shopify cancellation and refund reconciliation, GDPR redaction, token re-encryption command
- [ ] Shopify order edits (`orders/updated`)

## P8 Release
- [x] Dockerfiles + docker-compose, built and run in CI with a smoke test and a training job (MinIO optional)
- [~] Railway + Vercel deployment config with a predeploy migration hook (written, not deployed)
- [x] CI workflow: lint, type-check, migrations check, tests, build, pip-audit / npm audit, Playwright E2E, secret scan
- [x] Load test (Locust, `tests/load`) paced under rate limits with pass/fail gates; CI smoke run; single-flight evaluation cache (ADR-017)
- [x] Full platform flow test (`tests/e2e/test_full_platform_flow.py`)
- [x] Backup script (`scripts/backup.sh`) with restore drill; deployment, upgrade and rollback runbook
- [x] Release report (`docs/RELEASE_REPORT.md`)

## P9 Planning features (roadmap round 1, 2026-10-27)
- [x] Suppliers, with supplier lead time as a fallback for products (ADR-031)
- [x] Purchase orders: draft, approve, send, partial receive, cancel; drafts from reorder suggestions; CSV export (ADR-031)
- [x] Planner forecast overrides applied to decisions and scored against actuals (forecast value added, ADR-032)
- [x] Overstock and dead-stock report (ADR-033)
- [ ] E-mailing purchase orders to suppliers (needs an e-mail provider)
- [x] Multiple stock locations: per-location counts and uploads, transfers, purchase order delivery location (ADR-034)
- [x] Category and organisation forecasts that add up to product forecasts, bottom-up or top-down by backtest (ADR-035)
- [x] Scheduled digests to the app, Slack, webhooks or e-mail (SMTP), with SSRF-checked, encrypted targets (ADR-036)
- [x] Budget-constrained ordering with drafts from the plan (ADR-037)
- [x] Supplier price breaks and measured supplier reliability feeding safety stock (ADR-038)
- [x] Audit log export (CSV, JSON Lines) and per-organisation retention (ADR-039)
- [x] Two-factor login with an authenticator app, recovery codes and an organisation requirement (ADR-040)
- [x] Team invitations by one-time link (e-mailed when SMTP is set) and a getting-started checklist (ADR-041)
- [ ] Next roadmap items: data export and deletion, API keys
  and webhooks, launches, promotions, assistant actions, PDF reports, observability, UX polish, connectors;
  per-location demand needs sales by location

## Follow-ups
- [x] Build and run images in CI
- [ ] Container image scan (registry-side, ADR-026)
- [x] Daily purge of expired tokens, OAuth states, reset links and old webhook ids
- [x] `reencrypt-tokens` command for `TOKEN_ENCRYPTION_KEYS` rotation
- [x] Live forecast accuracy monitoring (ADR-028)
- [ ] Self-service password reset by e-mail
- [ ] Real Anthropic and Shopify runs; staging deployment and capacity test
