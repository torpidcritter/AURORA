# Aurora

Demand forecasting and inventory intelligence for retail catalogues. Aurora takes sales history (CSV/XLSX/JSON upload
or Shopify orders), forecasts demand per product with calibrated intervals, and turns the forecasts into reorder
points, stock-out risk, alerts and recommendations.

**Status:** feature-complete against the spec for a single-host deployment and covered by automated tests. It has not
run in production, and the Docker images and the Anthropic and Shopify integrations have not been run against real
services. See [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) for exactly what was verified.

## What it does
- **Forecasting:** five statistical baselines and a Transformer, chosen per product by rolling-origin backtests;
  split-conformal prediction intervals; MC-dropout uncertainty; permutation feature importance; category and
  organisation forecasts that add up to the product forecasts.
- **Inventory:** safety stock, reorder point and quantity (pack size, MOQ), days until stock-out, stock-out
  probability, urgency, a risk matrix and what-if scenarios; an overstock and dead-stock report.
- **Purchasing:** suppliers, purchase orders from draft to received (with approval, partial receipts and CSV export),
  drafts built from the reorder suggestions, a budget planner that spends a fixed amount where it avoids the most
  shortage, quantity price breaks, and supplier reliability (lead time, on-time and fill rate) measured from orders.
- **Locations:** stock per warehouse or store, transfers between them, and a delivery location on purchase orders.
- **Planner overrides:** adjust the forecast used for decisions over a date range, and see whether each adjustment
  beat the model once actual sales arrive.
- **Digests:** daily or weekly summaries in the app, Slack, a webhook or e-mail.
- **Operations:** background training and batch forecasting, a model registry with a promotion gate and rollback,
  drift and accuracy monitoring, deduplicated alerts.
- **Access:** organisations (tenants), four roles enforced by the API, an audit log with CSV/JSON Lines export and
  per-organisation retention, rate limits.
- **Integrations:** an assistant that answers questions with Claude over read-only, permission-checked tools (with a
  deterministic fallback), and Shopify install plus signed order webhooks.

## Layout
```
aurora-backend/    FastAPI, SQLAlchemy, Alembic, Celery, PyTorch       (Python 3.13)
aurora-frontend/   Next.js 16 app + backend-for-frontend auth layer    (Node 22)
docs/              plan, status, architecture decisions, runbook, testing
docker-compose.yml single-host stack: Postgres, Redis, API, worker, beat, web
```

## Quick start (Docker Compose)
```bash
cp .env.example .env     # fill in POSTGRES_PASSWORD, SECRET_KEY, BOOTSTRAP_ADMIN_PASSWORD
docker compose up -d --build
docker compose run --rm api python -m app.cli bootstrap
docker compose run --rm api python -m app.cli seed-demo     # optional synthetic data
```
Open http://localhost:3100. Full instructions, TLS, upgrades and backups: [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

## Local development
```bash
# Backend (needs PostgreSQL and Redis)
cd aurora-backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env     # set SECRET_KEY and DATABASE_URL
alembic upgrade head && python -m app.cli bootstrap
uvicorn app.main:app --reload --port 8100
celery -A app.workers.celery_app worker -Q default,training     # separate terminal

# Frontend
cd aurora-frontend && npm ci && npm run dev                     # http://localhost:3100
```

## Tests
Backend pytest, frontend Vitest, Playwright end-to-end and a Locust load test. Commands and coverage:
[`docs/TESTING.md`](docs/TESTING.md). CI runs all but the load test on every push.

## Documentation
- [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md): what is verified, known limitations
- [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md): backlog and progress
- [`docs/ARCHITECTURE_DECISIONS.md`](docs/ARCHITECTURE_DECISIONS.md): design decisions and deviations from the spec
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md): deployment, migrations, backup/restore, secrets, monitoring
- [`docs/TESTING.md`](docs/TESTING.md): running the test suites
- API reference: `http://localhost:8100/docs` when the API runs locally (not exposed through the web app)

## Security notes
Secrets come from the environment only; `.env` files are git-ignored. The browser never sees an access token: the
Next.js server keeps tokens in `HttpOnly` cookies and checks CSRF on every change. Report vulnerabilities privately to
the repository owner rather than in a public issue.
