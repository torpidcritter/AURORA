# Aurora Release Report

_Updated 2026-10-27 (first build 2026-10-23). Branch `feat/aurora-platform`, pull request #1._

## Summary
Aurora covers the spec's functional scope: data ingestion, per-product forecasting with calibrated intervals, inventory
intelligence, background jobs, a model registry with a promotion gate, monitoring with live accuracy and alert routing,
multi-tenant access control with an audit log, per-organisation settings, a Next.js web app with a floating assistant,
a Claude assistant and a Shopify integration.

Since the first build, the container images are built and run in CI, the runtime uses a restricted database role,
S3 storage is exercised against MinIO, and password reset, organisation settings, a whole-platform test, a gated load
test and a secret scan were added. The first roadmap round added suppliers, purchase orders (draft to received, with
drafts from reorder suggestions), planner forecast overrides scored against actual sales, and an overstock report. The second added multiple stock
locations with transfers and a delivery location on purchase orders, then category forecasts that add up to the
product forecasts and scheduled digests to the app, Slack, webhooks or e-mail. The third round started with a
budget planner for purchase orders, quantity price breaks, supplier reliability measured from purchase orders, and
audit log export with per-organisation retention, two-factor login with an authenticator app, team invitations with a getting-started checklist, and data export with account and organisation deletion.

It is suitable for a **staging trial on synthetic or consented data**. It is not yet ready for production use with
real customer data: neither the Anthropic nor the Shopify integration has run against the real service, it has never
been deployed to a hosted environment, capacity has not been measured on production-like hardware, and there has been
no external security review. The sections below give the evidence.

## Verification

| Check | Result |
|---|---|
| Backend tests (pytest, PostgreSQL), incl. the whole-platform flow | 452 passed |
| Frontend unit tests (Vitest) | 184 passed |
| Browser E2E (Playwright, live worker) | 19 passed locally; CI on PR #1 |
| Containers: images, Compose stack, restricted DB role, MinIO, smoke test with training, Locust smoke | CI on PR #1 |
| Lint, format, migration drift (`alembic check`), production build | clean |
| Dependency audit (pip-audit, npm audit high) | CI on PR #1 |
| Secret scan of tracked files | no findings |
| Backup and restore with `scripts/backup.sh` | restored with matching counts and checksums |

Commands: [`TESTING.md`](TESTING.md). Per-area evidence: [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md).

## Security controls in place
Each item is covered by an automated test unless marked otherwise.
- Every route requires a permission checked by the API; a test enumerates all routes × 4 roles + anonymous and fails
  if a new route is missing from the matrix.
- Every query is scoped to the caller's organisation; cross-tenant tests exist for each resource type.
- Passwords are hashed with bcrypt. Login is rate limited per e-mail and per client IP, with lockout and no user
  enumeration. Authenticated requests and uploads have per-user limits. Refresh tokens rotate, and reuse of an old one
  revokes the whole token family.
- Password reset links are admin-issued, hashed at rest, single use, short-lived and carried in the URL fragment.
- The browser never holds a token: `HttpOnly`, `SameSite=Strict` cookies, an Origin check and a CSRF header. The
  client address is trusted only from the BFF, with a shared secret.
- The API and worker connect as a role that cannot change the schema or rewrite audit entries (no UPDATE or
  TRUNCATE; retention deletes stay allowed). CI checks this in the running container.
- Uploads: type sniffing, size and row limits, zip-bomb guard.
- Shopify: validated shop domain, single-use OAuth state, constant-time HMAC checks, replay window, idempotent delivery,
  tokens encrypted at rest with rotatable keys, GDPR redaction.
- Assistant: read-only tools that run as the caller, permission re-checked per call, question text not stored.
- Secrets come only from the environment; production mode refuses default secrets and wildcard CORS. A CI job scans
  tracked files for credentials and for secret-looking `NEXT_PUBLIC_*` names (a guard, not a full history scan).
- Not done: penetration test, OS-package scan of the images, dependency pinning by hash, WAF or bot protection.

## Deviations from the spec
- Next.js 16 instead of 14, and a backend-for-frontend instead of NextAuth (ADR-004, ADR-014).
- Feature importance is permutation importance, not SHAP (ADR-012). External signals change forecasts only inside
  explicit scenarios (ADR-011).
- Password reset is admin-issued, not e-mailed (ADR-029). The live accuracy metric is configurable (ADR-028).
- Full list: [`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md).

## Known limitations
- Anthropic and Shopify are verified only against fakes. Railway/Vercel config has not been deployed.
- Purchase orders are not e-mailed to suppliers, and forecasts and reorder suggestions are organisation-wide (sales have no location).
- Shopify order edits are not reconciled; Shopify and uploaded sales for the same days are summed.
- Transformer training is CPU-bound and slow for large catalogues. The first forecast after new data takes seconds.
- Load results so far come from one small machine and say nothing about capacity.

## Before production
1. Run the assistant with a real key and Shopify against a development store; review the audit output.
2. Deploy to staging behind TLS, keep `/metrics` and `/docs` internal ([`RUNBOOK.md`](RUNBOOK.md)), and run the
   Locust test there.
3. Schedule `scripts/backup.sh` and rehearse a restore on the target host.
4. Turn on image scanning in the registry you deploy from.
5. Get an external security review.
