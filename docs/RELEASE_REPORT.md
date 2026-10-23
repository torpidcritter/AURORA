# Aurora Release Report — first build

_Date: 2026-10-23. Branch `feat/aurora-platform`, pull request #1._

## Summary
Aurora's first build covers the spec's functional scope for a single-host deployment: data ingestion, per-product
forecasting with calibrated intervals, inventory intelligence, background jobs, a model registry with a promotion
gate, monitoring and alerts, multi-tenant access control with an audit log, a Next.js web app, a Claude assistant and
a Shopify integration.

It is ready for a **staging trial on synthetic or consented data**. It is not yet ready for production use with real
customer data: the container images have never been built, and neither the Anthropic nor the Shopify integration has
run against the real service. The sections below give the evidence for each part.

## Verification at release

| Check | Result |
|---|---|
| Backend tests (pytest, PostgreSQL) | 175 passed |
| Frontend unit tests (Vitest) | 46 passed |
| End-to-end browser tests (Playwright, live worker) | 10 passed locally; CI result on PR #1 |
| Lint, type-check, migration drift (`alembic check`), production build | clean |
| Dependency audit (pip-audit, npm audit high) | no known vulnerabilities on 2026-10-23 |
| Backup/restore drill | data and model artifacts restored with matching counts and checksums |
| Load test (20 users, 89 s, one API process) | 0 failures; warm medians 11–130 ms; cold `/forecast` 7.5 s |

Commands and details: [`TESTING.md`](TESTING.md). Per-area evidence: [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md).

## Security controls in place
Each item is covered by an automated test unless marked otherwise.
- Every route requires a permission checked by the API; a test enumerates all routes × 4 roles + anonymous and fails
  if a new route is missing from the matrix.
- Every query is scoped to the caller's organisation; cross-tenant tests exist for each resource type.
- Passwords are hashed with bcrypt. Login is rate limited and locks out after repeated failures, with no user enumeration.
  Refresh tokens rotate, and reuse of an old one revokes the whole token family.
- The browser never holds a token: `HttpOnly`, `SameSite=Strict` cookies, plus an Origin check and a custom CSRF header.
- Uploads: type sniffing, size and row limits, zip-bomb guard.
- Shopify: validated shop domain, single-use OAuth state, constant-time HMAC checks, replay window, idempotent delivery,
  tokens encrypted at rest with rotatable keys.
- Assistant: read-only tools that run as the caller, permission re-checked per call, and the question text is not stored.
- Secrets come only from the environment. Production mode refuses default secrets and wildcard CORS. No `NEXT_PUBLIC_*`
  variables exist. A local Redis snapshot that had been committed by mistake was removed; it held only local test
  cache data.
- Not done: penetration test, container image scan, dependency pinning by hash, WAF or bot protection.

## Deviations from the spec
- Next.js 16 instead of 14, and a backend-for-frontend instead of NextAuth (ADR-004, ADR-014).
- Feature importance is permutation importance, not SHAP. Explanations are descriptive statistics, not invented causes
  (ADR-012).
- External signals change forecasts only inside explicit scenarios (ADR-011).
- Full list: [`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md).

## Known limitations
- Docker images and Compose stack are not built or run (the build environment had no Docker daemon).
- Anthropic and Shopify are verified only against fakes.
- Shopify refunds, cancellations and edits are not reconciled. Shopify and uploaded sales for the same days are summed.
- Expired refresh tokens and OAuth states are not purged.
- Transformer training is CPU-bound and slow for large catalogues. The first forecast after new data takes seconds.
- No self-service password reset.

## Before production
1. Build the images and run the Compose stack. Add an image build and scan job to CI.
2. Run the assistant with a real key and Shopify against a development store; review the audit output.
3. Put a TLS reverse proxy in front, set `FORWARDED_ALLOW_IPS`, and keep `/metrics` and `/docs` internal
   ([`RUNBOOK.md`](RUNBOOK.md) section 3).
4. Schedule backups of the database and the artifacts volume, and rehearse a restore on the target host.
5. Get an external security review.
