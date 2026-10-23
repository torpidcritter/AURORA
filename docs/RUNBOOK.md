# Aurora Operations Runbook

Scope: running Aurora on a single host with Docker Compose. This is the deployment shape the repository supports today.
Items marked **(not exercised)** are written but were not run in the build environment, which had no Docker daemon.

## 1. Components

| Service | What it does | Port |
|---|---|---|
| `db` | PostgreSQL 16, volume `pgdata` | internal |
| `redis` | Celery broker, rate limits, caches; volume `redisdata` | internal |
| `migrate` | Runs `alembic upgrade head` once, then exits | none |
| `api` | FastAPI (uvicorn), `/health/*`, `/metrics` | `127.0.0.1:8100` |
| `worker` | Celery worker, queues `default` and `training` | none |
| `beat` | Celery Beat: monitoring, stale-job reaper | none |
| `web` | Next.js app and the BFF (`/api/auth/*`, `/api/proxy/*`) | `127.0.0.1:3100` |

Model artifacts live in the `artifacts` volume (`/data/artifacts`). The registry stores a SHA-256 for each artifact and
refuses to load one whose checksum does not match.

## 2. First deployment (not exercised)

1. Copy the env template and generate secrets. Never commit `.env`.
   ```bash
   cp .env.example .env
   python -c "import secrets; print(secrets.token_urlsafe(48))"   # use for POSTGRES_PASSWORD and SECRET_KEY (different values)
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
enabled without `TOKEN_ENCRYPTION_KEYS`.

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

- **Application:** rebuild or retag the previous images and `docker compose up -d`. Only migration `0001` exists today.
  Before downgrading a future migration, restore the pre-upgrade backup rather than running `alembic downgrade` against
  production data.
- **Model:** in the app, open *Models* and click *Roll back*, or call `POST /models/registry/rollback`. This re-activates
  the previously active version, whose artifact must still exist in the `artifacts` volume.

## 6. Backup and restore

**Verified on 2026-10-23** against the local E2E database (`pg_dump`/`pg_restore` 16): all 18 tables restored with
identical row counts, the `sales_records` content checksum matched, and both model artifacts restored from a tarball
with SHA-256 values matching the registry. The `docker compose` wrappers below were not run.

Back up both the database and the artifacts volume, at the same time:
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
| `POSTGRES_PASSWORD` | Change the role password in Postgres first (`ALTER ROLE`), then `.env`, then restart. |
| `TOKEN_ENCRYPTION_KEYS` | Comma-separated Fernet keys, newest first. Add the new key at the front, restart; old tokens still decrypt. Reconnecting a shop re-encrypts its token with the newest key. A bulk re-encrypt command is not built yet. Remove the old key only after every shop has been re-encrypted. |
| `SHOPIFY_API_SECRET` | Put the old value in `SHOPIFY_API_SECRET_PREVIOUS` while Shopify switches over; HMACs are accepted with either. |
| `ANTHROPIC_API_KEY` | Replace and restart. Without a key the assistant answers from deterministic templates. |
| `METRICS_TOKEN` | Update Prometheus' bearer token at the same time. |

Never put secrets in `NEXT_PUBLIC_*` variables: the frontend has no such variables and must not gain any.

## 8. Monitoring

- Liveness `GET /health/live`; readiness `GET /health/ready` (DB, Redis and storage, 503 when one fails); summary `GET /health`.
- Prometheus: scrape `api:8100/metrics` from inside the network with `Authorization: Bearer $METRICS_TOKEN`. Useful series:
  `aurora_http_requests_total` and `aurora_http_request_duration_seconds` (by route template), `aurora_forecast_jobs_total`,
  `aurora_forecasts_total`, `aurora_training_duration_seconds`, `aurora_active_alerts_total`, `aurora_queue_depth`,
  `aurora_webhooks_total{outcome=processed|duplicate|bad_signature|error}`.
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
| Assistant says it is in fallback mode | Key missing, refused, or API error | Check `ANTHROPIC_API_KEY` and API logs. The fallback answers are deterministic and use the same authorised data |

## 10. Housekeeping not automated yet

- Expired rows in `oauth_states` and `refresh_tokens` are not purged. Delete rows past `expires_at` on a schedule
  (a deliberate SQL step; review before running).
- Shopify order edits, cancellations and refunds are not reconciled. Shopify sales and uploaded CSV sales for the same
  orders are summed, so do not upload both for the same period.
