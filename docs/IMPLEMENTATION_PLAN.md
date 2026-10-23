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
- [ ] Self-service password-reset email flow (needs an email provider; admin reset exists)

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
- [~] Real Celery worker + Redis broker run (eager mode is tested; live worker test is next)

## P6 Frontend (next)
- [ ] Next.js 14 app shell, BFF auth (httpOnly cookies, CSRF), typed API client
- [ ] Pages: login, dashboard, upload wizard, products, product detail, forecasts, inventory, risk matrix, scenarios,
      alerts, model diagnostics/drift, assistant, users, audit/ops
- [ ] Component tests (Vitest + Testing Library), Playwright E2E against the real backend

## P7 Integrations
- [ ] Claude assistant with authorised, validated tools + deterministic fallback
- [ ] Shopify OAuth (state, HMAC, shop validation), signed webhooks with replay/idempotency, order → sales mapping

## P8 Release
- [ ] Dockerfiles + docker-compose (Postgres, Redis, MinIO, API, worker, beat, frontend)
- [ ] CI workflow (lint, type-check, tests, build, pip-audit/npm audit, secret scan)
- [ ] Load test (Locust) with a bounded run; backup/restore drill; deployment & rollback runbook
- [ ] Release report
