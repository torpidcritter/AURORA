# Load tests (Locust)

`locustfile.py` simulates signed-in dashboard users: product and alert lists, latest forecasts, KPIs, on-demand
forecasts and batch stock-out scans. Pacing, account parsing and the pass/fail rules live in `load_support.py`
(standard library only), which `tests/unit/test_load_support.py` covers without installing Locust.

Run it against a local or staging stack only. Do not point it at production without sign-off, and use a test
tenant with synthetic data and a dedicated low-privilege account (a viewer can do every task here).

## Install

Locust patches the standard library with gevent, so keep it out of the API's environment:

```bash
cd aurora-backend
python -m venv .venv-load
.venv-load/bin/pip install -r requirements-load.txt
```

## Run

```bash
LOAD_EMAIL=viewer@test-tenant.example LOAD_PASSWORD='…' \
  .venv-load/bin/locust -f tests/load/locustfile.py --host http://localhost:8100 \
  --headless -u 20 -r 5 -t 3m --only-summary
```

Credentials come only from the environment. `LOAD_USERS="a@x:pw,b@x:pw"` spreads users over several accounts.

## Rate limits and pacing

The API rate limits each account, not each simulated user. All requests count towards
`API_RATE_LIMIT_PER_MINUTE` (600 by default), and some endpoints have their own budgets: KPI 30/min, forecast
60/min and batch risk 20/min. Login is limited per email. The load test paces each account at 90% of those limits
(`LOAD_PACING`), so it measures latency rather than 429s. When an endpoint budget is used up, the user does another
task instead, and the skipped counts are logged at the end.

To push more load, add accounts, or raise the limits on the test stack and pass the same values in `LOAD_API_RPM`
and `LOAD_LOGIN_RPM`. In a distributed run (`--master`/`--worker`) pacing is per worker process.

## Pass/fail

The process exits 1 when a threshold is broken, 2 when no credentials were given, and 0 otherwise.

| Variable | Default | Meaning |
|---|---|---|
| `LOAD_MAX_FAIL_RATIO` | 0.01 | Share of failed requests, sign-ins included |
| `LOAD_MAX_P95_MS` | 5000 | p95 of every request except sign-ins (bcrypt is deliberately slow) |
| `LOAD_P95_MS` | see below | Per-endpoint p95 limits; when set, replaces the defaults |
| `LOAD_TIMEOUT_S` | 60 | Per-request timeout |

Default per-endpoint p95 limits (ms) are sized for a small stack with cold caches: `GET /products` 1000,
`GET /alerts` 1000, `GET /forecast/latest` 1000, `GET /intelligence/kpi` 5000, `POST /forecast` 15000,
`POST /risk/stockout/batch` 15000. Tighten them for a warm staging run, for example
`LOAD_P95_MS="GET /products=300,POST /forecast=3000"`.

## What has been measured

Only a short local run so far: 5 users for 30 seconds against one uvicorn process on the development sandbox,
with 0 failures. That checks the script works; it says nothing about capacity. Run it on staging hardware before
quoting any throughput figure. See `docs/TESTING.md`.
