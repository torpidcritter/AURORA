# Testing Aurora

All automated tests use synthetic data. None of them need real customer, payment or production data, and none of
them call Anthropic or Shopify: those clients are replaced with fakes in tests.

## Backend (pytest)

Needs PostgreSQL with an `aurora_test` database (owner `aurora`). Redis is replaced by fakeredis and Celery runs in
eager mode.

```bash
cd aurora-backend
python -m venv .venv && . .venv/bin/activate
pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu   # or the PyPI wheel
pip install -r requirements.txt -r requirements-dev.txt
alembic upgrade head                                       # against DATABASE_URL
DATABASE_URL=postgresql+psycopg://aurora:aurora@localhost:5432/aurora_test pytest -q
ruff check app tests && ruff format --check app tests
alembic check                                              # models and migrations agree
pip-audit -r requirements.txt
```

`tests/conftest.py` forces `ENVIRONMENT=test`, a temporary artifact directory and fast bcrypt, and removes any
`ANTHROPIC_*`, `SHOPIFY_*` and `TOKEN_ENCRYPTION_KEYS` variables from the environment so a developer's real keys are
never used.

| Suite | Covers |
|---|---|
| `tests/unit/` | Ingestion rules, statistical models and metrics, conformal intervals, Transformer, inventory formulas |
| `tests/integration/test_auth.py`, `test_rbac_matrix.py` | Login, tokens, lockout, rate limits, every route × every role |
| `tests/integration/test_datasets.py`, `test_forecasting_api.py` | Uploads, products, inventory concurrency, forecasts, risk, scenarios, signals |
| `tests/integration/test_jobs_models_alerts.py` | Jobs, registry, monitoring, alerts, audit, tenant isolation |
| `tests/integration/test_assistant.py` | Tool authorisation, tenant scoping, fallback on refusal/errors, audit without message text |
| `tests/integration/test_shopify.py` | OAuth state/HMAC/shop validation, webhook signatures, freshness, idempotency, rollback-on-failure, token encryption and rotation |
| `tests/integration/test_eval_single_flight.py` | Concurrent forecast requests compute the expensive evaluation once |

## Frontend (Vitest, ESLint, TypeScript)

```bash
cd aurora-frontend
npm ci
npm run typecheck && npm run lint && npm test
npm run build
npm audit --audit-level=high
```

Unit tests cover input validation, the API client (error envelope, CSRF header, retry on a superseded refresh), the BFF
(CSRF origin check, path allow-list, blocked operational paths, refresh and cookie rotation, shared concurrent refresh,
right-most `X-Forwarded-For` hop) and components.

## End-to-end (Playwright)

Runs a real browser against the built frontend, the API and a Celery worker, on a throwaway database.

```bash
# 1. Backend on a dedicated database (never a real tenant's)
export DATABASE_URL=postgresql+psycopg://aurora:aurora@localhost:5432/aurora_e2e
export REDIS_URL=redis://localhost:6379/5
export SECRET_KEY=$(openssl rand -base64 48)
export BOOTSTRAP_ADMIN_EMAIL=admin@example.com BOOTSTRAP_ADMIN_PASSWORD="E2e-$(openssl rand -hex 12)"
cd aurora-backend && alembic upgrade head && python -m app.cli bootstrap && python -m app.cli seed-demo
uvicorn app.main:app --port 8100 &
celery -A app.workers.celery_app worker -Q default,training -c 2 &

# 2. Frontend
cd ../aurora-frontend && npm run build && API_URL=http://127.0.0.1:8100 npx next start -p 3100 &

# 3. Tests
E2E_ADMIN_EMAIL=$BOOTSTRAP_ADMIN_EMAIL E2E_ADMIN_PASSWORD=$BOOTSTRAP_ADMIN_PASSWORD npx playwright test
```

Set `PLAYWRIGHT_CHROMIUM_PATH` to use an already-installed Chromium. The suite signs in once and shares the session,
because login is rate limited to 5 attempts per minute per account. If you re-run it many times within a minute,
clear the `rl:*` keys in that Redis database.

The 10 scenarios cover: redirect to login and bad-password rejection; login setting `HttpOnly` cookies that
`document.cookie` cannot read; CSRF rejection by the BFF; product detail (forecast, backtest, drivers, recommendation,
a stock edit); recommendations, risk matrix and a scenario run; signals add/delete; a real training job through the
Celery worker; an admin creating a viewer who is then refused admin pages and writes; the assistant answering in
built-in mode; and logout.

## Load test (Locust)

The load test lives in `aurora-backend/tests/load/` (`locustfile.py`, helpers in `load_support.py`); see
`tests/load/README.md` for pacing, thresholds and exit codes. Run it only against a local or staging stack, from its
own virtualenv (`requirements-load.txt`), with credentials in the environment:

```bash
cd aurora-backend
LOAD_USERS="load0@example.com:pw,load1@example.com:pw" \
  .venv-load/bin/locust -f tests/load/locustfile.py --host http://localhost:8100 --headless -u 20 -r 5 -t 90s
```

The script paces each account under the API's per-account rate limits and fails the run (exit 1) on a failure ratio
above 1% or a p95 over its limits. CI runs a 30-second, 5-user smoke against the Compose stack with loose latency
gates: it proves the API holds up under concurrent use, not how much traffic it can take.

The figures below were measured with the earlier version of the script (`loadtest/locustfile.py`, which called the
old `GET /forecast`). Re-measure on staging hardware before quoting capacity.

Latest local result (2026-10-23): 4-CPU sandbox, one uvicorn process, one Celery worker, 12 synthetic products,
20 users for 89 s, two runs back to back. 0 failures in 1,391 requests.

| Endpoint | Cold cache median / p95 (ms) | Warm cache median / p95 (ms) |
|---|---|---|
| `GET /forecast` | 7,500 / 27,000 | 130 / 280 |
| `GET /intelligence/kpi` | 2,300 / 4,000 | 100 / 290 |
| `GET /products` | 330 / 760 | 14 / 100 |
| `GET /alerts` | 420 / 830 | 11 / 96 |
| All requests | 500 / 5,000 | 41 / 270 |

"Cold" means the backtest cache for every product was empty when the run started. Before the single-flight lock
(ADR-017), the cold `/forecast` median was 46 s, because every concurrent request recomputed the same backtests.
These numbers come from one small machine and are not a capacity estimate for production.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request: backend (ruff, migrations + `alembic check`, pytest,
pip-audit), frontend (typecheck, lint, Vitest, build, npm audit) and, after both pass, the Playwright suite against
PostgreSQL and Redis service containers with secrets generated for that run.
