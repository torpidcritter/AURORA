# Aurora Operations Runbook

Scope: running Aurora on a single host with Docker Compose. This is the deployment shape the repository supports today.
The build sandbox has no Docker daemon. The Compose stack is exercised in CI instead (job *Containers*: build, start,
bootstrap, seed, smoke test with a training job, restricted DB role and MinIO storage checks). Items marked
**(not exercised)** were written but not run anywhere.

## 1. Components

| Service | What it does | Port |
|---|---|---|
| `db` | PostgreSQL 16, volume `pgdata` | internal |
| `redis` | Celery broker, rate limits, caches; volume `redisdata` | internal |
| `migrate` | As the schema owner: `alembic upgrade head`, then `app.cli grant-app-role`; exits | none |
| `api` | FastAPI (uvicorn), `/health/*`, `/metrics` | `127.0.0.1:8100` |
| `worker` | Celery worker, queues `default` and `training` | none |
| `beat` | Celery Beat: monitoring, stale-job reaper | none |
| `web` | Next.js app and the BFF (`/api/auth/*`, `/api/proxy/*`) | `127.0.0.1:3100` |
| `minio` | Optional (`COMPOSE_PROFILES=s3`): S3-compatible storage for development | console `127.0.0.1:9001` |

**Database logins.** `migrate` connects as the schema owner `aurora` (`POSTGRES_PASSWORD`). `api`, `worker` and `beat`
connect as `aurora_app` (`APP_DB_PASSWORD`), which `grant-app-role` creates and refreshes after every migration. It can
read and write rows, cannot create, alter, drop or truncate tables, cannot edit `audit_events` (insert and retention
expiry only) and cannot write `alembic_version`. The owner password is not passed to the long-running containers.
Existing volumes pick the role up on the next `docker compose up` because `migrate` runs the grant every time.

Model artifacts and uploaded files live in the `artifacts` volume (`/data/artifacts`), or in S3 when
`STORAGE_BACKEND=s3` (the bucket is created on first use). The registry stores a SHA-256 for each artifact and
refuses to load one whose checksum does not match.

## 2. First deployment (exercised in CI)

1. Copy the env template and generate secrets. Never commit `.env`.
   ```bash
   cp .env.example .env
   python -c "import secrets; print(secrets.token_urlsafe(48))"   # POSTGRES_PASSWORD, APP_DB_PASSWORD, SECRET_KEY: three different values
   ```
   Set `BOOTSTRAP_ADMIN_PASSWORD` (at least 10 characters with three of: lowercase, uppercase, digit, symbol) and `PUBLIC_URL` (the URL users open).
2. Build and start:
   ```bash
   docker compose up -d --build
   docker compose ps          # api healthy, migrate exited 0
   ```
3. Create the first admin once, then remove `BOOTSTRAP_ADMIN_PASSWORD` from `.env`:
   ```bash
   docker compose run --rm api python -m app.cli bootstrap
   ```
4. Optional demo data (synthetic, clearly labelled, never for a real tenant):
   `docker compose run --rm api python -m app.cli seed-demo --products 12 --days 240`

The backend refuses to start with `ENVIRONMENT=production` and a default or short `SECRET_KEY`, wildcard CORS, or Shopify
enabled without `TOKEN_ENCRYPTION_KEYS`. It also refuses `AUTH_ENABLED=false`, `DB_ENABLED=false` and any `DB_TYPE`
other than `postgresql`: the spec's variable names are accepted (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`,
`DB_PASSWORD`, `ADMIN_PASSWORD`, `ARTIFACT_ARTIFACTS_DIR`), but those modes are not supported. `DATABASE_URL`,
`BOOTSTRAP_ADMIN_PASSWORD` and `ARTIFACT_DIR` win when both forms are set.

Optional settings in `.env` (log level, rate limits, S3, Anthropic model, Shopify webhook address, training limits) are
passed to the backend containers; empty values mean the application default. See `.env.example`.

**Object storage (optional).** For the bundled MinIO set `COMPOSE_PROFILES=s3`, `STORAGE_BACKEND=s3`,
`S3_ENDPOINT_URL=http://minio:9000`, `S3_ACCESS_KEY` and `S3_SECRET_KEY` (8+ characters). MinIO then uses those as its
root credentials, which is acceptable for a development box only. In production use a managed S3 bucket and a key
limited to that bucket. Switching backends does not copy existing files: existing models must be retrained or copied.
MinIO no longer publishes community images (`minio/minio` is gone from Docker Hub), so the profile uses
`bitnamilegacy/minio:2025.7.23-debian-12-r5`, Bitnami's last build, which receives no security fixes. It is fine for a
development box; for anything shared use managed S3 or a maintained S3-compatible server.

## 3. Reverse proxy, TLS and client addresses

Compose binds ports to `127.0.0.1` only. For remote users, put a TLS-terminating reverse proxy in front:

- Route everything to `web:3100`, except `/shopify/callback` and `/shopify/webhooks/*`, which go to `api:8100` (Shopify
  calls the API directly).
- **Do not** expose `/metrics`, `/docs`, `/redoc` or `/openapi.json` publicly. The BFF already refuses to proxy them.
- The proxy must **overwrite** `X-Forwarded-For` with the real client address (for nginx:
  `proxy_set_header X-Forwarded-For $remote_addr;`). The BFF and uvicorn take the right-most hop, so a client cannot
  spoof its address for rate limiting, provided the proxy appends or overwrites the header.
- `api` runs uvicorn with `--proxy-headers --forwarded-allow-ips="*"`. That is safe only while port 8100 is reachable
  solely from the compose network and the reverse proxy. If you expose 8100 any other way, set `FORWARDED_ALLOW_IPS` to
  the proxy's address.

Session cookies are `HttpOnly`, `SameSite=Strict` and, in the production web image, always `Secure`. Browsers accept
Secure cookies on `http://localhost`, but any other address needs HTTPS or login will appear to do nothing.

## 4. Upgrades and migrations

Migrations change the live schema, so they are a deliberate, reviewed step:

1. Take a backup (section 6) and confirm it restores.
2. Read the new migration files in `aurora-backend/alembic/versions/`.
3. Pull the new code and build images: `docker compose build`.
4. Apply migrations: `docker compose run --rm migrate`. Watch the output and stop if it fails.
5. Restart the app: `docker compose up -d api worker beat web`.
6. Check `curl -fsS http://127.0.0.1:8100/health/ready` and sign in.

`docker compose up` also runs `migrate` automatically. On a production host, prefer the explicit sequence above.

CI checks that the models and migrations agree (`alembic check`).

## 5. Rollback

- **Application:** rebuild or retag the previous images and `docker compose up -d`.
  Migrations `0002` and `0003` only add a table and columns; `0005` adds the supplier, purchase order and override
  tables and a nullable `products.supplier_id`, so code from before it still runs against the upgraded schema. `0006` adds locations, stock levels and transfers and
  backfills a `MAIN` location; product totals are kept, but downgrading drops the per-location detail. `0007` adds
  the digest tables only; `0008` adds a nullable `products.price_breaks` column. Before downgrading, restore the pre-upgrade backup rather
  than running `alembic downgrade` against production data.
- **Model:** in the app, open *Models* and click *Roll back*, or call `POST /models/registry/rollback`. This re-activates
  the previously active version, whose artifact must still exist in the `artifacts` volume.

## 6. Backup and restore

**Verified on 2026-10-23** against the local E2E database (`pg_dump`/`pg_restore` 16): all 18 tables restored with
identical row counts, the `sales_records` content checksum matched, and both model artifacts restored from a tarball
with SHA-256 values matching the registry. The `docker compose` wrappers below were not run.

**Recovery objectives (proposed, confirm with the business owner):**

| | Target | How |
|---|---|---|
| RPO (data you can lose) | 24 h with nightly dumps; minutes if the database provider offers point-in-time recovery (enable it) | `scripts/backup.sh` from cron, or the provider's backups |
| RTO (time to restore service) | 2 h for a single host | restore the latest dump into a new database, restore artifacts, switch over (below) |
| Retention | 14 daily dumps on the host (`RETENTION_DAYS`), plus monthly copies kept off-host for 12 months | copy off-host after each run |

Models can be retrained from the restored sales history, so a lost artifact costs time, not data. Uploaded files
are also kept in the database as parsed rows; the stored file is only needed to re-run an import.

`scripts/backup.sh` dumps the database in custom format, refuses to keep a dump that `pg_restore --list` cannot read,
tars the artifacts directory when `ARTIFACT_DIR` is set, and prunes its own files older than `RETENTION_DAYS`. Tested on
2026-10-26 against the local E2E database: the dump restored into a new database with identical `sales_records` counts
and migration version, and only expired backup files were pruned. Example cron entry on the Compose host:
```bash
15 2 * * * cd /opt/aurora && BACKUP_DATABASE_URL="postgresql://aurora:$(cat /opt/aurora/.pgpass-owner)@127.0.0.1:5432/aurora" BACKUP_DIR=/var/backups/aurora scripts/backup.sh >> /var/log/aurora-backup.log 2>&1
```
That needs the database port published to the host (it is not by default); otherwise use the `docker compose exec`
form below. **A restore drill must be repeated before launch and after major upgrades** (spec section 11).

Manual equivalent with Compose, database and artifacts volume at the same time:
```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T db pg_dump -U aurora -Fc --no-owner aurora > aurora-$TS.dump
docker run --rm -v aurora_artifacts:/a:ro -v "$PWD":/out alpine tar -C /a -czf /out/artifacts-$TS.tgz .
```
Store backups off-host and encrypted. They contain business data and password hashes. Redis does not need a backup:
it holds caches, rate-limit counters and the job broker, and durable job state lives in Postgres.

Restore into a **new** database first and check it before switching over:
```bash
docker compose exec -T db createdb -U aurora aurora_restore
docker compose exec -T db pg_restore -U aurora --no-owner --exit-on-error -d aurora_restore < aurora-$TS.dump
docker compose exec -T db psql -U aurora -d aurora_restore -c "select count(*) from sales_records"
```
To switch over, stop `api worker beat`, rename the databases (or point `DATABASE_URL` at the restored one), restore the
artifacts tarball into the `artifacts` volume, and start the services again. Dropping or renaming the live database is
destructive: do it only with the owner's sign-off.

## 7. Secrets and key rotation

| Secret | Effect of rotating |
|---|---|
| `SECRET_KEY` | Signs JWT access tokens and keys the refresh-token hashes. Rotating it signs everyone out. If `TOKEN_ENCRYPTION_KEYS` is unset in development, the Shopify token key is derived from it. |
| `POSTGRES_PASSWORD` | Owner login, used by `migrate` only. Change it in Postgres first (`ALTER ROLE aurora PASSWORD ...`), then `.env`. |
| `APP_DB_PASSWORD` | Runtime login. Update `.env`, then `docker compose run --rm migrate` (sets the new password) and `docker compose up -d api worker beat`. Expect a short gap while they restart. |
| `TOKEN_ENCRYPTION_KEYS` | Comma-separated Fernet keys, newest first. Add the new key at the front, restart; old tokens still decrypt. Then run `docker compose run --rm api python -m app.cli reencrypt-tokens`; it exits non-zero and names any shop it could not decrypt (reconnect those). Remove the old key only after it reports no failures. |
| `SHOPIFY_API_SECRET` | Put the old value in `SHOPIFY_API_SECRET_PREVIOUS` while Shopify switches over; HMACs are accepted with either. |
| `ANTHROPIC_API_KEY` | Replace and restart. Without a key the assistant answers from deterministic templates. |
| `METRICS_TOKEN` | Update Prometheus' bearer token at the same time. |

Never put secrets in `NEXT_PUBLIC_*` variables: the frontend has no such variables and must not gain any.

## 8. Monitoring

- Liveness `GET /health/live`; readiness `GET /health/ready` (DB, Redis and storage, 503 when one fails); summary `GET /health`.
- Prometheus: scrape `api:8100/metrics` from inside the network with `Authorization: Bearer $METRICS_TOKEN`. Useful series:
  `aurora_http_requests_total` and `aurora_http_request_duration_seconds` (by route template, summed across all uvicorn
  workers), `aurora_webhooks_total{outcome=processed|duplicate|bad_signature|rejected|error}`. Signed deliveries that are rejected
  (too old, malformed) or fail also appear in the shop owner's *Audit log* as `shopify.webhook_failed`; unsigned requests
  are only counted. Figures produced by the worker are
  read from Postgres and Redis at scrape time (cached 15 s), so they are right whichever process did the work:
  `aurora_forecast_jobs_total{kind,status}`, `aurora_jobs_in_progress{kind}`, `aurora_forecasts_total{method}`,
  `aurora_training_duration_seconds`, `aurora_active_alerts_total`, `aurora_queue_depth{queue}`. Being derived from
  stored rows, the totals drop if old rows are deleted; alert on rates over short windows.
- Logs are JSON on stdout with a `request_id`, which is also returned in the `X-Request-ID` header and in error bodies.
- In-app: *System & integrations* shows queue, job and component status; *Audit log* shows security-relevant actions.

## 9. Common incidents

| Symptom | Check | Action |
|---|---|---|
| Login does nothing | Browser on a non-localhost http URL | Serve over HTTPS (section 3) |
| `503` when starting a training or batch job | `docker compose ps redis`, `/health/ready` | Restart Redis, then start the job again. A job that could not be queued is marked failed, not left queued |
| Jobs stuck in `running` | Worker logs, *System & integrations* | Restart `worker`. Every 10 minutes Beat marks jobs with no recent heartbeat as failed; start them again |
| Slow first forecast for a product | Expected: it backtests baselines once per data fingerprint | Run *Batch forecast* after uploads to warm the cache |
| Shopify webhooks rejected (401) | `aurora_webhooks_total{outcome="bad_signature"}` rising; API log "invalid signature" | Check `SHOPIFY_API_SECRET`; during rotation set `SHOPIFY_API_SECRET_PREVIOUS` |
| A user is locked out or forgot their password | *Users* page, audit log `auth.login` failures | An admin opens *Users*, chooses *Reset link* and passes the link on over a trusted channel (it is shown once, works once and expires after `PASSWORD_RESET_TTL_MINUTES`, default 60). Admins cannot issue one for themselves, so keep at least two admin accounts: there is no command-line recovery for a sole admin who forgot their password yet |
| "This reset link is invalid" | Audit log `auth.password_reset` failures show the real reason (expired, used, superseded, inactive user) | Issue a new link; issuing one cancels older ones |
| Alerts appear in the app but not in the webhook | *Settings* minimum severity, `ALERT_MIN_SEVERITY` | Only alerts at or above the organisation's minimum (default `high`) are sent to channels; lower ones stay in the app |
| Stock on order looks wrong after purchasing | *Purchase orders* (filter *Open*), audit log `purchase_order.*` | `on_order` changes only when an order is sent, received or cancelled. Correct a mistake by cancelling the order (releases what is outstanding) or editing the product's stock, which is audited |
| "The order was changed by someone else" | Someone acted on the same order | Reload the order and check it before trying again; nothing was applied |
| "Choose a location for …" on a count or upload | The organisation has more than one active location | Pick the location on the product's *Stock by location* card, or add a `location` column with location codes to the upload |
| Location totals do not match a stock take | *Locations*, *Recent transfers*, audit log `stock.transferred` and `inventory.update` | Count each location and save the count there; transfers between locations never change the product total |
| A digest shows "failed" | *Digests* page, *View* on the digest shows the error; logs `aurora.digests` | Check the webhook still exists (Slack returns 404 for a removed one), or SMTP settings for e-mail. Fix the target and use *Send now*. Scheduled sends are not retried |
| Digests are not sent at all | Celery beat running? Task `aurora.run_due_digests` in worker logs | Start beat (`celery -A app.workers.celery_app beat`); schedules whose slot passed are sent on the next tick |
| Assistant says it is in fallback mode | Key missing, refused, or API error | Check `ANTHROPIC_API_KEY` and API logs. The fallback answers are deterministic and use the same authorised data |

## 10. Housekeeping

Beat runs these daily; each can also be run by hand:

| Task | Schedule | Manual command |
|---|---|---|
| Delete refresh tokens expired over a day ago, OAuth states expired over an hour ago, spent or expired password reset links over a day old, processed webhook ids older than `WEBHOOK_EVENT_RETENTION_DAYS` (30) | 03:30 UTC | `docker compose run --rm api python -m app.cli purge-expired` |
| Delete audit events older than each organisation's retention (Settings, default `AUDIT_RETENTION_DAYS` = 365); writes one `audit.retention_purge` event per organisation | 03:15 UTC | none (task `aurora.cleanup_audit_logs`) |
| Mark jobs without a recent heartbeat as failed | every 10 minutes | none |

## 11. Shopify app setup

1. In the Shopify Partner dashboard, set the app URL and the allowed redirect URL to `SHOPIFY_REDIRECT_URL`
   (`https://<api host>/shopify/callback`).
2. Configure the three mandatory compliance webhooks there (`customers/data_request`, `customers/redact`, `shop/redact`)
   to `https://<api host>/shopify/webhooks/<topic>`, e.g. `/shopify/webhooks/shop/redact`. Aurora stores no customer data;
   `shop/redact` deletes the shop's token and its order ledger (order ids and lines). Daily per-SKU totals already in
   the tenant's sales history stay with the organisation.
3. Set `SHOPIFY_WEBHOOK_BASE_URL` to the public HTTPS origin of the API if it differs from the redirect URL's origin.
   After install Aurora subscribes to `orders/create`, `orders/cancelled`, `refunds/create` and `app/uninstalled`.
   Shopify accepts only HTTPS addresses, so on a plain-HTTP development setup the card shows "webhooks not registered"
   with the reason; *Re-register webhooks* retries after you fix the address.
4. Refunds remove only restocked units from the sales history; cancellations remove what is left of the order. Shopify
   retries failed deliveries for about four hours; deliveries older than `SHOPIFY_WEBHOOK_TOLERANCE_S` (12 h) are refused.

## 12. Cloud deployment: Railway (API, worker, beat) and Vercel (web) (not exercised)

The repository contains the configuration; no Railway or Vercel project was available during the build, so this
path has not been run.

**Railway**, one project:

1. Add PostgreSQL and Redis from the Railway catalogue.
2. Create three services from this repository, each with root directory `aurora-backend` and a config file path
   (the path is from the repository root): `api` → `/aurora-backend/railway.json`, `worker` →
   `/aurora-backend/railway.worker.json`, `beat` → `/aurora-backend/railway.beat.json`. Keep `beat` at one replica.
3. Variables (use Railway's secret variables; reference syntax shown):

   | Variable | api | worker, beat |
   |---|---|---|
   | `ENVIRONMENT` | `production` | `production` |
   | `DB_HOST`, `DB_PORT`, `DB_NAME` | `${{Postgres.PGHOST}}`, `${{Postgres.PGPORT}}`, `${{Postgres.PGDATABASE}}` | same |
   | `DB_USER`, `DB_PASSWORD` | `aurora_app`, a generated value | same |
   | `APP_DB_PASSWORD` | same generated value | not needed |
   | `MIGRATION_DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (owner, used by the pre-deploy step) | **do not set** |
   | `REDIS_URL` | `${{Redis.REDIS_URL}}` | same |
   | `SECRET_KEY`, `TOKEN_ENCRYPTION_KEYS` | generated | same values |
   | `STORAGE_BACKEND=s3`, `S3_*` | a bucket and a key limited to it | same |
   | `BFF_SHARED_SECRET` | generated, 32+ characters | not needed |
   | `FRONTEND_URL`, `API_CORS_ORIGINS` | the Vercel URL | `FRONTEND_URL` only |
   | `SHOPIFY_*`, `ANTHROPIC_API_KEY`, `METRICS_TOKEN` | as needed | `ANTHROPIC_*`, `SHOPIFY_*` as needed |

   On the first deploy the pre-deploy step (`python -m app.cli predeploy`) migrates as the owner and creates
   `aurora_app`; the app then connects as that role. Railway gives the pre-deploy step the API service's variables, so
   the owner URL is present in the API's environment (not in the worker's or beat's). That is weaker than Compose, where
   only the one-off `migrate` container has it; a separate migration job would remove it.
4. Object storage must be S3 on Railway: the API and the worker run in separate containers with no shared disk, and
   container disks are wiped on redeploy.
5. Expose only the `api` service publicly (Shopify needs `/shopify/*`). `/metrics` needs `METRICS_TOKEN`; `/docs` is
   available on the public API domain, so block it at an edge proxy if that matters to you.

**Vercel**: import the repository with root directory `aurora-frontend` (framework Next.js). Set server-side variables
only: `API_URL` = the Railway API's public URL and `BFF_SHARED_SECRET` = the same value as on the API. There is no
`NEXT_PUBLIC_API_URL` and no NextAuth: the browser only talks to the Next.js server, which holds the session in HttpOnly
cookies and calls the API (ADR-004). Vercel overwrites `X-Forwarded-For` with the real client address; the BFF passes it
to the API with the shared secret so rate limits and the audit log see each user rather than Vercel's addresses.
