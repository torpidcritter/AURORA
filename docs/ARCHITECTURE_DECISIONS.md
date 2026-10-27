# Architecture Decisions

Short records of significant decisions. Each entry states the context, the decision, and its consequences.
The product reference is `Aurora_Technology_Architecture_and_Specifications.pdf` ("the spec").

## ADR-001 Monorepo with a modular FastAPI backend and a Next.js frontend
- **Context:** the repository was empty. The spec names `aurora-backend/` and `aurora-frontend/`.
- **Decision:** one repository with `aurora-backend/` (FastAPI, SQLAlchemy 2, Alembic, Celery) and `aurora-frontend/`
  (Next.js App Router; version 16, see ADR-014). The backend is one deployable plus separate Celery worker and Beat processes, not microservices.
- **Layers:** `api/routes` (HTTP only) → `services` (use cases, transactions) → `domain` and `ml` (pure logic) → `db` / `storage`.

## ADR-002 PostgreSQL only, synchronous SQLAlchemy in a threadpool
- **Decision:** PostgreSQL is the only supported database (the spec's SQLite-in-development option is dropped). Partial unique
  indexes, `ON CONFLICT` upserts and `FOR UPDATE SKIP LOCKED` are used for correctness under concurrency, and SQLite cannot
  provide those semantics. Tests run against a real PostgreSQL test database.
- Endpoints are sync `def`, so FastAPI runs them in its threadpool and blocking DB calls never block the event loop.
  CPU-heavy training runs only in Celery workers.
- No local JSON files hold shared state (the spec's "runtime JSON repositories" are not implemented).

## ADR-003 Multi-tenant by organisation
- Every tenant-owned row carries `org_id`. Every service query filters by the caller's `org_id`. Objects from another
  organisation return **404** (not 403) so their existence is not revealed. Covered by cross-tenant tests.

## ADR-004 Authentication: backend-issued JWT + rotating opaque refresh tokens; frontend uses a BFF instead of NextAuth
- Access token: HS256 JWT, 30 min, with `iss`, `aud`, `typ`, and a `tv` (token version) claim. The user row is re-checked on
  every request, so deactivation, role change and password change take effect immediately.
- Refresh token: 384-bit random opaque string, stored only as a keyed BLAKE2b hash, 7-day expiry, rotated on every use.
  Each login starts a token *family*. Reusing a rotated token more than 20 s after rotation revokes the whole family
  (theft detection). A replay inside 20 s (two tabs refreshing at once) is rejected without revoking the family.
- Passwords: bcrypt with cost 12, applied to a SHA-256 pre-hash so passphrases longer than 72 bytes are not truncated. The
  strength policy requires 10+ characters and 3 character classes.
- Brute force protection: Redis-backed rate limits per IP and per email (shared across instances), plus a durable per-account
  lockout in PostgreSQL (8 failures → 15 minutes).
- **Frontend deviation from the spec:** NextAuth is not used. The Next.js server acts as a backend-for-frontend (BFF). It keeps
  both tokens in `httpOnly`, `SameSite=Strict`, `Secure` (in production) cookies, proxies API calls, and refreshes tokens on
  the server. Tokens never reach client JavaScript. Mutating proxy calls require same-origin `Origin` and a custom header (CSRF
  defence). NextAuth's credential provider would add a second session layer around the same backend tokens without adding
  security.

## ADR-005 Forecasting design
- **Transformer:** follows the spec's architecture (Linear→64, sinusoidal PE, 3 encoder layers, 4 heads, dropout 0.1,
  last-token head 64→32→H). **Deviation:** the product identifier is a learned `nn.Embedding` (index 0 = unknown) concatenated to
  each time step, instead of a standard-scaled numeric ID. An arbitrary SKU integer has no ordinal meaning.
  The 4 continuous channels are per-product standard-scaled sales, day-of-week/6, (month−1)/11 and a weekend flag.
- **Leakage controls:** each product's series is split chronologically into train | early-stop | calibration. The
  held-out segments are sized in forecast windows, not days, so every horizon gets the same number of windows:
  early-stop H+7 days (8 windows), calibration H+28 days (29 windows), i.e. 14 and 35 days at H=7 and 63 and 84 days at
  H=56. (Fixed 14/35-day segments left no complete window at H=28 or 56; fixed 2026-10-26, older bundles still load.)
  Scalers are fit on train only. Targets never cross segment boundaries (unit-tested).
- **Selection:** the Transformer is never assumed superior. Training compares it with SMA, SES, seasonal-naive, Holt-Winters and
  Croston-SBA on the *same* held-out windows. A version passes the promotion gate only if it beats seasonal-naive and is no more
  than 5% worse than the best baseline overall. The `auto` method uses the Transformer only for products where it beat every
  baseline on held-out data. Otherwise it uses the baseline with the lowest rolling-origin (K=4) WAPE.
- **Measured result (synthetic data, 20 SKUs × 365 d):** Transformer WAPE 12.7 vs Holt-Winters 12.4. The Transformer won on 8
  of 20 products. This is why per-product selection exists.
- **Horizons:** baselines support 1–90 days. A Transformer is trained for one horizon (7/14/28/56). Requests beyond the trained
  horizon are refused with a reason, not silently extended.
- **Intermittent demand:** Croston-SBA is included. Holt-Winters needs ≥ 28 observations, otherwise it falls back to seasonal-naive.

## ADR-006 Uncertainty
- **Prediction intervals:** split-conformal on absolute residuals, per horizon step, with the finite-sample quantile
  `ceil((n+1)(1−α))/n`. If a step has too few residuals, residuals are pooled across steps ≤ h (this only widens intervals).
  Baselines calibrate on up to 42 dense rolling origins. The Transformer calibrates on its calibration segment
  (29 windows: 35 days at H=7).
- **Coverage is measured, not promised:** each interval reports empirical coverage from calibrating on the first 2/3 of origins
  and testing on the last 1/3. Time-series errors are only approximately exchangeable, so no coverage guarantee is claimed.
- **MC dropout:** sets the model to eval mode, then re-enables only `nn.Dropout` modules, with 20 samples. PyTorch's fused
  Transformer "fast path" skips dropout in eval mode, so it is disabled during sampling (otherwise MC dropout would be silently
  deterministic; this is unit-tested). MC spread is labelled as a heuristic epistemic signal, never as a prediction interval.
- Supported coverages: 0.8, 0.9, 0.95.

## ADR-007 Inventory math (see `app/domain/inventory.py`)
- `SS = z · σ_d · √L`, where σ_d is the SD of the daily forecast error (the RMSE of the chosen method's recent errors, with a
  fallback to the 56-day demand SD) and z = Φ⁻¹(cycle service level). With lead-time variability:
  `SS = z · √(L σ_d² + μ_d² σ_L²)`. The spec's `SS = Z·σ_d·L` was not used (it is dimensionally inconsistent for daily σ).
- `ROP = μ_d · L + SS`. Order-up-to `S = μ_d(L+C) + SS`. Order `S − (on_hand + on_order)` only when the inventory position is
  ≤ ROP, rounded up to the pack size and raised to the MOQ. **Deviation:** the spec's ROQ omits demand during the lead time.
- Days-until-stockout walks cumulative forecast demand against on-hand stock. Stockout probability is
  `P(D_L > on_hand)` under a normal approximation. Open orders are excluded from both because their arrival date is unknown.
- Urgency = 0.40·S_time + 0.35·S_prob + 0.25·S_inv, with S_time = 100·clip(1 − DTS/(2L)), S_prob = 100·P(stockout in L),
  S_inv = 100·clip(1 − IP/ROP). Levels follow the spec thresholds.
- Products without known on-hand stock get **no** recommendation (status `unknown_inventory`), never a guess.

## ADR-008 Missing dates vs zero sales
- Explicit zero rows are kept as confirmed zeros. Dates absent from uploads are filled according to a per-product
  `missing_policy`: `zero` (default; typical POS exports omit no-sale days) or `interpolate` (absence = unknown). Imputed days are
  flagged in the history API and counted in the quality report.

## ADR-009 Background jobs
- Job state lives in PostgreSQL (`jobs`), not in Celery results. A worker claims a job with an atomic `queued→running` update.
  Duplicate deliveries (`acks_late` redelivery, retries) therefore cannot run a job twice.
- Transient DB/connection errors are retried up to 3 attempts with exponential backoff. Other errors fail the job with the error
  recorded. A reaper marks `running` jobs with no heartbeat for 2 h as failed. If the broker is unavailable at enqueue time, the
  job is marked failed and the API returns 503.
- At most one queued/running training job per organisation, enforced by a partial unique index.
- Beat runs as its own process. Scheduled per-org jobs use an hourly idempotency key, so a re-fired tick does not duplicate work.

## ADR-010 Alerts
- Alerts are deduplicated by `(org_id, dedup_key)` among unresolved alerts (partial unique index + upsert). Severity only
  escalates automatically, and an escalation reopens an acknowledged alert. Alerts auto-resolve when their condition clears.
- Delivery claims a unique `(alert_id, channel)` row before sending, so retries never double-deliver. The webhook sends the
  alert id as `Idempotency-Key`. After 5 failed attempts the delivery is marked `dead`.
- Monitoring *recommends* retraining (alerts for drift, staleness and accuracy). It never auto-promotes a model.
- Routing: open alerts at or above a minimum severity are sent to the channels (log, and the webhook when configured).
  The minimum is `ALERT_MIN_SEVERITY` (default `high`, the original high/critical rule), overridable per organisation
  (ADR-030). The rule is evaluated inside the dispatch query, so one organisation's low alerts never fill the batch
  ahead of another's deliverable ones. Every alert stays visible in the app whatever its severity.

## ADR-011 External signals are explicit, not silently applied
- Signals (magnitude −1…+1) are stored and shown alongside forecasts. They change numbers only in a scenario where the user picks
  a sensitivity (`multiplier = 1 + magnitude × sensitivity`). Aurora does not invent effect sizes for holidays or weather.

## ADR-012 Explainability
- Feature importance is **permutation importance** (ΔMAE in units/day, 5 repeats) on the calibration windows, labelled as such.
  It is not SHAP. Per-product "drivers" are descriptive statistics (weekend lift with a Mann-Whitney test, linear trend with a
  p-value, level change, volatility, intermittency, data gaps). No festival or weather narratives are generated.

## ADR-013 Python 3.13 / dependency versions
- The sandbox ships Python 3.13, and the CPU-only PyTorch index is blocked by its network policy, so the standard PyPI torch
  wheel (2.14) is used. Versions are pinned in `requirements.txt` from a working install.

## ADR-014 Frontend: Next.js 16, React 19, Tailwind 4, SWR; the browser never holds a token
- The spec says Next.js 14. The current stable line (16.4) was used instead, because 14 is outside its support window and would ship without security fixes.
  Visible differences: route protection lives in `proxy.ts` (the renamed middleware), dynamic `params` are Promises,
  and Tailwind 4 is configured in CSS.
- The browser talks only to the same-origin BFF. `/api/auth/*` sets `aurora_at`/`aurora_rt` as `HttpOnly`,
  `SameSite=Strict` cookies (always `Secure` in production builds). `/api/proxy/*` adds the bearer token server-side,
  refreshes once on 401, and refuses `metrics`, `docs`, `redoc` and `openapi.json`.
- CSRF: a mutation must carry a matching `Origin` and the custom `x-aurora-csrf: 1` header, which a cross-site form
  cannot send. `SameSite=Strict` is a second layer.
- The UI hides actions a role cannot perform, but that is a convenience. Every permission is enforced by the API, and
  the E2E suite checks that a viewer's direct write calls fail with 403.
- No `NEXT_PUBLIC_*` variables exist. `API_URL` is read only on the server (`import "server-only"`).

## ADR-015 Assistant: Claude with read-only, permission-checked tools and a deterministic fallback
- `POST /chat` runs a manual tool loop (at most 5 rounds) over four read tools: products, risk summary,
  alerts, drift. Each tool runs as the calling user, inside their organisation, and re-checks the permission even
  though the tool list is already filtered by role. The model cannot write data.
- Model `claude-opus-5-5` with `effort` configurable (default low) and the server-side fallback beta enabled, so an
  overloaded model falls back on Anthropic's side before our own fallback is needed.
- Without an API key, or on a refusal, an empty answer or an API error, the endpoint answers from deterministic
  templates over the same tools and says so (`mode: "fallback"`). It never invents numbers.
- The audit log records the mode and the tools used, never the question text, which may contain business details.
- Rate limit: 20 requests per minute per user. Message up to 2,000 characters; up to 10 history turns, normalised to
  alternate roles.

## ADR-016 Shopify: verified installs, signed idempotent webhooks, encrypted tokens
- Install: shop domain must match `^[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com$`; the OAuth `state` is single-use,
  expires in 10 minutes and is consumed atomically; the callback HMAC and timestamp are checked before the code is
  exchanged over HTTPS. A shop connected to one organisation cannot be claimed by another.
- Webhooks: the body HMAC is checked first (constant-time, current or previous secret to allow rotation). Deliveries
  older than one hour are refused. The webhook id is stored in the same transaction as the sales upsert, so a
  redelivery is a no-op and a failed delivery rolls back fully and is retried by Shopify.
- Orders become daily sales rows with `source="shopify"`; new SKUs are created with `inventory_known=false` so no
  reorder advice is given until stock is entered. Test orders are skipped. `app/uninstalled` deletes the access token;
  `shop/redact` also erases the shop's order ledger; the customer GDPR topics are acknowledged because Aurora stores no
  customer data.
- Access tokens are encrypted with Fernet keys from `TOKEN_ENCRYPTION_KEYS` (newest first, older keys still decrypt).
  Production refuses to start with Shopify enabled and no keys.
- Not done: edits, cancellations and refunds are not reconciled; Shopify and uploaded sales for the same period add up.

## ADR-017 Shared, single-flight cache for forecast evaluation
- Choosing a method for a product needs rolling-origin backtests of every baseline, which takes seconds. Under load,
  concurrent requests for the same product all recomputed it (cold `/forecast` median 46 s at 20 users).
- The result is now cached in Redis (`aurora:eval:{org}:{product}:{horizon}:{data fingerprint}`, 24 h) on top of the
  existing database cache, and a Redis lock makes one request compute while the others wait and then read the cache.
  The data fingerprint changes when sales change, so stale results are never served.
- If Redis is down, the code computes without the lock (fails open). Correctness does not depend on the lock; only
  duplicate work does.

## ADR-018 Client addresses behind proxies
- Rate limits and audit entries use the client address. The BFF forwards only the **right-most** `X-Forwarded-For`
  entry, which is the one the trusted reverse proxy added; anything to its left is client-controlled. The API trusts
  forwarded headers only from the addresses in uvicorn's `--forwarded-allow-ips`.
- In Docker Compose the API port is reachable only from the compose network and `127.0.0.1`, so all sources are
  trusted there. Any other deployment must narrow `FORWARDED_ALLOW_IPS` to its proxy (see `RUNBOOK.md`).
- When the web app and the API run on different platforms (Vercel + Railway), the API is reachable from the internet
  and cannot trust `X-Forwarded-For` from arbitrary callers. The BFF then also sends the client address in
  `X-Aurora-Client-IP` together with `X-Aurora-BFF: <BFF_SHARED_SECRET>`; the API believes the address only when the
  secret matches (constant-time compare) and the value parses as an IP address. Browsers cannot set these headers
  through the BFF, which forwards an allowlist of request headers. Without the secret, a split deployment would put
  every user behind Vercel's few addresses into one login rate-limit bucket.

## ADR-019 Two database logins: schema owner for migrations, restricted role at runtime
- The API, worker and beat connect as `aurora_app`, which has row-level DML on the application tables only. It cannot
  create, alter, drop or truncate tables, cannot update `audit_events` (insert, and delete for retention expiry) and
  cannot write `alembic_version`. A SQL injection or code-execution bug in the app therefore cannot change the schema
  or silently rewrite the audit trail.
- `python -m app.cli grant-app-role` (Compose `migrate`) or `predeploy` (Railway) runs as the owner after every
  migration and re-applies the grants, so new tables are covered; default privileges cover tables created in between.
  The role is created without superuser, createdb, createrole, replication or bypassrls, and an existing role with any
  of those attributes is refused rather than silently used.
- Postgres row-level security was considered for tenant isolation and not adopted: tenant scoping is enforced in the
  service layer and covered by tests; RLS would add a second mechanism that every query and migration must respect.

## ADR-020 Deployment targets and variable names
- Supported: Docker Compose on one host (exercised in CI), and Railway (API, worker, beat) + Vercel (web), which the
  spec names (configuration in `aurora-backend/railway*.json`, not exercised).
- The spec's variable names (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `ADMIN_PASSWORD`,
  `ARTIFACT_ARTIFACTS_DIR`) are accepted alongside the names used in code. Modes the spec allows but Aurora does not
  support are refused at startup instead of ignored: `AUTH_ENABLED=false`, `DB_ENABLED=false`, non-Postgres `DB_TYPE`.
- The spec's `NEXT_PUBLIC_API_URL` and `NEXTAUTH_SECRET` are deliberately not used (ADR-004): the browser never calls
  the API directly and holds no token, so the frontend has no public variables. `API_URL` and `BFF_SHARED_SECRET` are
  server-side only.
- Object storage: local disk for one host, S3-compatible otherwise. The bundled MinIO (Compose profile `s3`) is for
  development; CI runs the stack with it so the S3 path is exercised.

## ADR-021 Statistical baselines and libraries
- Holt-Winters is statsmodels' `ExponentialSmoothing` with an **additive damped** trend and additive weekly seasonality.
  The spec says "linear trend"; an undamped trend extrapolates indefinitely and over-forecasts at 28-90 day horizons, so the
  damped form is used. It needs 28 observations and falls back to seasonal-naive below that.
- scikit-learn, which the spec lists for evaluation, is not a dependency. The metrics (WAPE, MAPE with an explicit zero
  exclusion rule, RMSE, bias), split-conformal intervals and permutation importance are short NumPy functions with unit
  tests; pulling in scikit-learn for them would add a large dependency and its CVE surface for no capability.
- MAPE is undefined for zero actuals (spec section 11). Aurora reports it over non-zero days with the number of excluded
  days, and uses WAPE for model selection and the promotion gate.

## ADR-022 Model artifacts: one `.pt` bundle, no pickle files
- The spec stores feature scalers as `.pkl`. Unpickling executes code, so a tampered artifact would be remote code
  execution in the worker. Aurora stores weights, configuration, per-product scaler statistics (mean, std as plain
  numbers) and metrics in one bundle written with `torch.save` and read with `torch.load(weights_only=True)`, which refuses
  arbitrary objects. The registry also records a SHA-256 of each bundle and refuses to load one that does not match.

## ADR-023 Module names
The spec names files that do not exist here; the responsibilities live in these modules:

| Spec | Aurora |
|---|---|
| `statistical_forecast.py` | `app/ml/statistical.py` |
| `transformer_model.py` | `app/ml/transformer_model.py`, training in `app/ml/training.py` |
| `uncertainty_engine.py`, `backtesting_service.py` | `app/ml/conformal.py`, `app/ml/backtesting.py` |
| `shap_service.py` | `app/services/explain_service.py` (permutation importance; not SHAP, see ADR-012) |
| `ingestion_pipeline.py` | `app/services/ingestion.py`, `app/services/dataset_service.py` |
| `drift_monitor.py` | `app/ml/drift.py`, `app/services/monitoring_service.py` |
| `model_registry_service_v2.py` | `app/services/model_registry.py` |
| `scenario_service.py` | `app/services/scenario_service.py` |
| `service.py` (assistant) | `app/services/assistant_service.py` |
| `shopify_routes.py` | `app/api/routes/shopify.py`, `app/services/shopify_service.py` |

## ADR-024 Plain PostgreSQL, no TimescaleDB
- The spec lists TimescaleDB. The largest table is daily sales per product (`sales_records`), queried by organisation and
  date range through a composite index. At the scale Aurora targets (thousands of SKUs, a few years of daily rows: low
  millions of rows) plain PostgreSQL serves those queries in milliseconds, and managed hosts (Railway, most others) do not
  offer the extension. Revisit with hypertables or declarative partitioning by date if tenants reach hundreds of millions
  of rows.

## ADR-025 Assistant route
- The assistant page is `/assistant`; `/chat` (the spec's page name) redirects there. The API endpoint is `POST /chat` as
  the spec says. A floating panel offers the same assistant on every page.

## ADR-026 Image and dependency scanning
- CI runs `pip-audit` on the backend requirements and `npm audit --audit-level=high` (all dependencies) on the
  frontend. Container images are built and run in CI as uid 10001 but not scanned for OS-package CVEs: the common GitHub
  Action for that (trivy-action) is a third-party action with a history of supply-chain compromise, and the owner asked
  for none. Scan images in the registry you deploy from (for example GitHub's or the cloud provider's registry scanning),
  or add a scanner pinned by commit SHA after review.

## ADR-027 Readiness and model artifacts
- The spec has `/health/ready` check "artifact integrity". Readiness fails (503) only when the database, Redis or storage
  is down. Active model files are checked for presence (cached five minutes) and reported as `models`, without failing
  readiness: one organisation's missing file would otherwise take the API out of rotation for every tenant, while
  forecasts for that organisation fall back to the statistical baselines. The full SHA-256 check runs when a model is
  loaded (ADR-022).

## ADR-028 Live accuracy
- Validation metrics are measured once, at training time. Monitoring also scores the forecasts users were actually served
  against the sales that arrived later: for each product and day, the freshest forecast made before that day; only days
  up to the product's last uploaded sale. The spec's trigger is MAPE above 25%; because MAPE skips zero-demand days and
  swings on low-volume products, the metric (MAPE or WAPE) and threshold are configurable. An alert recommends review and
  retraining; it never retrains or promotes on its own (ADR-010).

## ADR-029 Password reset: admin-issued one-time links, no e-mail
- Aurora has no e-mail service, so a self-service "forgot password" flow would have no safe channel. Instead an admin
  issues a one-time link for a user of their own organisation (`POST /auth/users/{id}/reset-link`) and passes it on
  over a channel they trust. The link is shown once.
- The token is 256 bits from `secrets`; only its SHA-256 is stored. It travels in the URL fragment
  (`/reset-password#token=…`), which browsers never send to a server or put in a `Referer` header.
- A link expires after `PASSWORD_RESET_TTL_MINUTES` (default 60) and is single use (a conditional `UPDATE` claims it).
  Issuing a new link, changing the password or deactivating the user expires the others. A successful reset ends
  every session of the user and clears any lockout.
- An admin cannot issue a link for their own account (a stolen admin session must not bypass the current password).
- Failures return one generic message and are audited with the real reason. The public endpoint is rate limited per
  client IP and fails closed if Redis is down.
- Revisit when an e-mail provider is configured: a self-service request flow can reuse the same token table.

## ADR-030 Per-organisation settings
- Inventory defaults (lead time, coverage, service level), monitoring thresholds (drift KS and mean change, model age,
  live accuracy metric and threshold) and the alert minimum severity can be overridden per organisation. Overrides are
  stored in `Organization.settings` (JSONB); a missing key means the global default from the environment applies.
- `GET /settings/org` (any role) returns each setting's effective value, the global default and whether it is
  overridden, with labels and bounds from the schema so the UI does not duplicate them. `PATCH /settings/org` needs the
  admin-only `manage_settings` permission; `null` clears an override. Unknown keys and loosely typed values are
  rejected. Updates lock the organisation row and are audited with before/after values.
- A stored value that no longer validates (for example after a bound is tightened) is ignored on read and dropped on
  the next write, rather than breaking monitoring for that organisation.
- Kept global on purpose: the validation WAPE gate for promotion (model quality is a platform rule) and the live
  accuracy window.


## ADR-031 Suppliers and purchase orders
- A supplier holds contacts, a lead time and a minimum order value. A product links to at most one supplier; its own
  lead time wins, then the supplier's, then the organisation default (the recommendation lists which one was used).
- Orders move draft → approved → sent → (partially received →) received, or are cancelled before they are fully
  received. Lines change only in a draft. Approval is a separate permission (`approve_purchase_orders`, analyst and
  admin) from creating, sending and receiving (`manage_purchasing`, operator and up), so one person need not do both.
- Stock moves only on `send` (quantities added to `on_order`) and `receive` (moved from `on_order` to `on_hand`);
  cancelling a sent order removes only what is still outstanding. Every transition locks the order row and checks the
  version the user saw, and products are locked in id order, so double clicks and concurrent orders cannot apply a
  change twice or deadlock. Order numbers are serialised per organisation on its row, with a unique constraint behind.
- "Mark as sent" records a fact; Aurora does not e-mail suppliers (no e-mail provider, see ADR-029). The CSV export
  neutralises cells that start with `=`, `+`, `-`, `@`, tab or carriage return so a spreadsheet will not run them.
- Drafting from suggestions uses the same recommendations as the inventory page, one draft per supplier (products
  without a supplier share one draft), and skips products already on a draft or approved order. Sent orders are not
  skipped because their stock is already counted in `on_order`. Each line keeps the suggestion it came from.

## ADR-032 Planner forecast overrides and forecast value added
- An override covers a date range of one product and either sets demand to a number of units per day or scales the
  model's forecast. A reason is required, ranges per product may not overlap (checked under a product row lock), and
  an override can be archived but not edited or deleted, so its history stays reviewable.
- Overrides are applied where forecasts feed decisions (reorder maths, risk, suggested orders) and shown beside the
  model forecast in `POST /forecast` (`adjusted_points`, `overrides`). The stored model forecast is never rewritten,
  and adjusted days drop the interval bounds, which describe the model's error rather than the planner's.
- Forecast value added (FVA) scores each override on the days it covered that now have actual sales: model WAPE (from
  the freshest forecast served before each day, as in live accuracy, ADR-028) minus override WAPE, in percentage
  points. More than +0.5 counts as helped, below −0.5 as hurt. Future overrides are listed as pending.
- Scenarios (ADR-011) are unchanged: they remain what-if views and never change stored or decision forecasts.

## ADR-033 Overstock and dead-stock report
- Computed on request from the trailing 90 days of sales relative to the organisation's latest sales date, like the
  KPIs. Days of cover = on hand ÷ average daily sales. Excess = stock above `cover_days` (default 180) of that demand.
  Dead stock = no sale in `dead_days` (default 90), or never. Products with unknown inventory or nothing on hand are
  left out.
- Suggestions are descriptive (pause reordering, review stock on order, consider clearance). Aurora has no price or
  elasticity data, so it does not propose discounts or predict their effect.

## ADR-034 Multiple stock locations
- A location (warehouse, store) has a code that is unique per organisation ignoring case, a name and an optional
  address. Each organisation has exactly one default location (partial unique index); it is created as `MAIN` the
  first time it is needed. `stock_levels` holds on hand and on order per product and location.
- The product's `on_hand` and `on_order` stay as the totals, and every change to a level updates them in the same
  transaction under the product row lock, so the sum of levels always equals the totals. Forecasting, recommendations,
  risk and overstock keep reading the totals and are unchanged. Demand stays organisation-wide because sales (uploads
  and Shopify) carry no location; per-location reorder suggestions would need location-level sales.
- Products that had stock before locations existed are adopted lazily: their totals become the default location's
  level the first time their levels are read or changed. Migration `0006` also backfills this for existing data.
- With one active location, counts and inventory uploads work as before. With several, a count must name a location
  and an upload needs a `location` column (aliases `location_code`, `warehouse`, `store`, `site`); otherwise the API
  refuses rather than guessing. Upload rows are parsed in full before anything is applied, so a bad row changes nothing.
- A transfer moves on-hand units between two active locations of one product under the product lock, so concurrent
  transfers cannot oversell a location; it is recorded in `stock_transfers` and the audit log. Totals do not change.
- A purchase order delivers to one location (the default unless chosen). Sending, receiving and cancelling move stock
  at that location. A location cannot be deactivated while it is the default, holds stock or has open orders.
- Managing locations is an admin action (`manage_settings`); counts and transfers need `manage_inventory`.
