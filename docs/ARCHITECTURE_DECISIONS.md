# Architecture Decisions

Short records of significant decisions. Each entry states the context, the decision, and its consequences.
The product reference is `Aurora_Technology_Architecture_and_Specifications.pdf` ("the spec").

## ADR-001 Monorepo with a modular FastAPI backend and a Next.js frontend
- **Context:** the repository was empty. The spec names `aurora-backend/` and `aurora-frontend/`.
- **Decision:** one repository with `aurora-backend/` (FastAPI, SQLAlchemy 2, Alembic, Celery) and `aurora-frontend/`
  (Next.js 14 App Router). The backend is one deployable plus separate Celery worker and Beat processes, not microservices.
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
- **Leakage controls:** each product's series is split chronologically into train | early-stop (14 d) | calibration (35 d).
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
  Baselines calibrate on up to 42 dense rolling origins. The Transformer calibrates on its 35-day calibration segment.
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
