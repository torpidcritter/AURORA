"""Load test for the read-heavy API paths a dashboard user hits.

Run against a local or staging stack only (never production without sign-off):

    AURORA_EMAIL=... AURORA_PASSWORD=... locust -f loadtest/locustfile.py --host http://localhost:8100 \
        --headless -u 20 -r 5 -t 2m --csv loadtest/results

Each simulated user logs in once and reuses the token (login is rate limited per account by design).
Use a dedicated load-test account; one account shared by many users trips the per-account login limit,
so create several and list them in AURORA_USERS as "email:password,email:password".
"""

from __future__ import annotations

import itertools
import os
import random
import threading

from locust import HttpUser, between, events, task

_users = [u.split(":", 1) for u in os.environ.get("AURORA_USERS", "").split(",") if ":" in u]
if not _users and os.environ.get("AURORA_EMAIL"):
    _users = [[os.environ["AURORA_EMAIL"], os.environ.get("AURORA_PASSWORD", "")]]
_cycle = itertools.cycle(_users) if _users else None
_lock = threading.Lock()


@events.test_start.add_listener
def _check(environment, **_):
    if _cycle is None:
        raise SystemExit("Set AURORA_USERS or AURORA_EMAIL/AURORA_PASSWORD")


class DashboardUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self) -> None:
        with _lock:
            email, password = next(_cycle)  # type: ignore[arg-type]
        r = self.client.post("/auth/login", json={"email": email, "password": password}, name="/auth/login")
        r.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        prods = self.client.get("/products?limit=100", name="/products").json()["items"]
        self.product_ids = [p["id"] for p in prods] or [None]

    @task(5)
    def kpi(self) -> None:
        self.client.get("/intelligence/kpi", name="/intelligence/kpi")

    @task(3)
    def products(self) -> None:
        self.client.get("/products?limit=50", name="/products")

    @task(3)
    def alerts(self) -> None:
        self.client.get("/alerts?status=open&limit=5", name="/alerts")

    @task(2)
    def trend(self) -> None:
        self.client.get("/intelligence/demand-trend?days=90", name="/intelligence/demand-trend")

    @task(2)
    def forecast(self) -> None:
        pid = random.choice(self.product_ids)
        if pid:
            self.client.post("/forecast", json={"product_id": pid, "horizon": 14}, name="/forecast")

    @task(1)
    def history(self) -> None:
        pid = random.choice(self.product_ids)
        if pid:
            self.client.get(f"/products/{pid}/history?days=180", name="/products/{id}/history")
