"""Locust load test for the Aurora API (spec 10.5). See tests/load/README.md for thresholds and examples.

Run against a local or staging stack only, never production without sign-off:

    LOAD_EMAIL=... LOAD_PASSWORD=... locust -f tests/load/locustfile.py --host http://localhost:8100

Credentials come only from the environment (LOAD_EMAIL / LOAD_PASSWORD, or LOAD_USERS="email:password,..." for
several accounts). Use a dedicated low-privilege account on a test tenant; a viewer is enough for every task here.

Each simulated user signs in once (POST /auth/login) and then behaves like a dashboard user. The API limits each
*account* (not each simulated user): every authenticated request counts towards API_RATE_LIMIT_PER_MINUTE (600 by
default), and some endpoints have their own per-account budgets (KPI 30/min, forecast 60/min, batch risk 20/min,
login 10/min per email). All simulated users of one account share those budgets, so this file paces requests per
account below the limits (LOAD_PACING, default 90%) instead of measuring 429s. To push more load, add accounts to
LOAD_USERS, or raise API_RATE_LIMIT_PER_MINUTE / LOGIN_RATE_LIMIT_PER_MINUTE on the test stack and pass the same
values in LOAD_API_RPM / LOAD_LOGIN_RPM. The per-endpoint budgets are fixed in the API code.

Pass/fail: at exit the run fails (exit code 1) when the failure ratio exceeds LOAD_MAX_FAIL_RATIO (default 0.01) or
the p95 of all requests except sign-ins exceeds LOAD_MAX_P95_MS (default 5000), or any endpoint's p95 exceeds its
limit (defaults in load_support.DEFAULT_ENDPOINT_P95_MS; LOAD_P95_MS replaces them, e.g.
LOAD_P95_MS="POST /forecast=5000,GET /products=500").

In a distributed run (--master/--worker) the pacing is per worker process: divide the budgets between workers.
"""

from __future__ import annotations

import itertools
import logging
import os
import random
import sys
import threading

import gevent
from locust import HttpUser, between, events, task
from locust.exception import RescheduleTaskImmediately
from locust.runners import MasterRunner, WorkerRunner

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load_support import (  # noqa: E402  (needs the path above)
    DEFAULT_API_RPM,
    DEFAULT_ENDPOINT_P95_MS,
    DEFAULT_LOGIN_RPM,
    DEFAULT_MAX_FAIL_RATIO,
    DEFAULT_MAX_P95_MS,
    ENDPOINT_LIMITS_PER_MINUTE,
    LOGIN_NAME_PREFIX,
    EndpointResult,
    Pacer,
    check_thresholds,
    parse_accounts,
    parse_p95_limits,
)

log = logging.getLogger("aurora.load")
REQUEST_TIMEOUT_S = float(os.environ.get("LOAD_TIMEOUT_S", "60"))


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


class Account:
    def __init__(self, email: str, password: str) -> None:
        self.email = email
        self.password = password
        pacing = _env_float("LOAD_PACING", 0.9)
        self.api = Pacer(_env_float("LOAD_API_RPM", DEFAULT_API_RPM) * pacing, burst=2)
        self.login = Pacer(
            _env_float("LOAD_LOGIN_RPM", DEFAULT_LOGIN_RPM) * pacing, burst=_env_float("LOAD_LOGIN_BURST", 4)
        )
        self.endpoints = {name: Pacer(limit * pacing) for name, limit in ENDPOINT_LIMITS_PER_MINUTE.items()}


ACCOUNTS = [
    Account(e, p)
    for e, p in parse_accounts(
        os.environ.get("LOAD_USERS"), os.environ.get("LOAD_EMAIL"), os.environ.get("LOAD_PASSWORD")
    )
]
_next_account = itertools.cycle(ACCOUNTS) if ACCOUNTS else None
_cycle_lock = threading.Lock()
paced_skips: dict[str, int] = {}


class DashboardUser(HttpUser):
    """A signed-in dashboard user: lists, KPIs, alerts and latest forecasts, sometimes a forecast or a risk scan."""

    wait_time = between(1, 4)

    def on_start(self) -> None:
        self.product_ids: list[str] = []
        if _next_account is None:
            return  # _require_credentials stops the run
        with _cycle_lock:
            self.account: Account = next(_next_account)
        self._login()
        r = self._get("/products?limit=100", name="/products (start-up)")
        if r is not None and r.ok:
            self.product_ids = [p["id"] for p in r.json().get("items", [])]
        if not self.product_ids:
            log.warning("No products visible to %s: POST /forecast tasks are skipped", self.account.email)

    # --- plumbing ---------------------------------------------------------------------------------------------------

    def _login(self) -> None:
        self.client.headers.pop("Authorization", None)
        body = {"email": self.account.email, "password": self.account.password}
        for attempt in range(1, 4):
            self.account.login.take()
            with self.client.post(
                "/auth/login", json=body, name="/auth/login", catch_response=True, timeout=REQUEST_TIMEOUT_S
            ) as r:
                if r.status_code == 200:
                    self.client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
                    return
                if r.status_code == 429 and attempt < 3:
                    # Another process (or an earlier run) used this account's login budget: wait as told, retry.
                    r.request_meta["name"] = "/auth/login (429, retried)"
                    r.success()
                    gevent.sleep(float(r.headers.get("Retry-After", "10")) + random.uniform(0, 2))
                    continue
                r.failure(f"login failed: HTTP {r.status_code}")
                return

    def _request(self, method: str, path: str, name: str, endpoint: str | None = None, **kw):  # type: ignore[no-untyped-def]
        if "Authorization" not in self.client.headers:
            return None
        if endpoint is not None and not self.account.endpoints[endpoint].try_take():
            paced_skips[endpoint] = paced_skips.get(endpoint, 0) + 1
            raise RescheduleTaskImmediately()  # over this account's budget for the endpoint: do something else
        self.account.api.take()
        with self.client.request(method, path, name=name, catch_response=True, timeout=REQUEST_TIMEOUT_S, **kw) as r:
            if r.status_code == 401:
                r.failure("401: session expired or revoked")
                self._login()
            elif r.status_code == 429:
                r.failure(f"429 rate limited (retry after {r.headers.get('Retry-After')}s): pacing too loose?")
            elif not r.ok:
                r.failure(f"HTTP {r.status_code}")
            return r

    def _get(self, path: str, name: str, endpoint: str | None = None):  # type: ignore[no-untyped-def]
        return self._request("GET", path, name, endpoint)

    def _post(self, path: str, body: dict, name: str, endpoint: str | None = None):  # type: ignore[no-untyped-def]
        return self._request("POST", path, name, endpoint, json=body)

    # --- tasks (weights approximate a dashboard session) ------------------------------------------------------------

    @task(4)
    def products(self) -> None:
        self._get(f"/products?limit=50&offset={random.choice([0, 0, 0, 50])}", name="/products")

    @task(3)
    def alerts(self) -> None:
        self._get("/alerts?status=active&limit=20", name="/alerts")

    @task(3)
    def latest_forecasts(self) -> None:
        self._get("/forecast/latest", name="/forecast/latest")

    @task(2)
    def kpi(self) -> None:
        self._get("/intelligence/kpi", name="/intelligence/kpi", endpoint="kpi")

    @task(2)
    def forecast(self) -> None:
        if not self.product_ids:
            raise RescheduleTaskImmediately()
        body = {"product_id": random.choice(self.product_ids), "horizon": random.choice([7, 14, 28])}
        self._post("/forecast", body, name="/forecast", endpoint="forecast")

    @task(1)
    def stockout_batch(self) -> None:
        self._post("/risk/stockout/batch", {}, name="/risk/stockout/batch", endpoint="risk-batch")


@events.test_start.add_listener
def _require_credentials(environment, **_kw) -> None:  # type: ignore[no-untyped-def]
    if ACCOUNTS or isinstance(environment.runner, MasterRunner):
        return
    log.error("Set LOAD_EMAIL and LOAD_PASSWORD (or LOAD_USERS) before running the load test")
    environment.process_exit_code = 2
    gevent.spawn(environment.runner.quit)


# --- pass/fail thresholds ---------------------------------------------------------------------------------------------


def _result(entry) -> EndpointResult:  # type: ignore[no-untyped-def]
    p95 = entry.get_response_time_percentile(0.95) if entry.num_requests else 0.0
    return EndpointResult(entry.num_requests, entry.num_failures, float(p95 or 0.0))


def evaluate(stats) -> list[str]:  # type: ignore[no-untyped-def]
    """Threshold violations for a finished run (empty when it passed); the rules live in load_support."""
    from locust.stats import StatsEntry

    by_name = {f"{e.method} {e.name}": e for e in stats.entries.values()}
    # Sign-ins are excluded from the overall latency gate: bcrypt makes them slow on purpose.
    rest = StatsEntry(stats, "all except sign-in", "", use_response_times_cache=False)
    for name, entry in by_name.items():
        if not name.startswith(LOGIN_NAME_PREFIX):
            rest.extend(entry)
    raw = os.environ.get("LOAD_P95_MS", "").strip()
    return check_thresholds(
        {name: _result(e) for name, e in by_name.items()},
        overall=_result(stats.total),
        overall_excluding_login=_result(rest),
        max_fail_ratio=_env_float("LOAD_MAX_FAIL_RATIO", DEFAULT_MAX_FAIL_RATIO),
        max_p95_ms=_env_float("LOAD_MAX_P95_MS", DEFAULT_MAX_P95_MS),
        endpoint_p95_ms=parse_p95_limits(raw) if raw else DEFAULT_ENDPOINT_P95_MS,
    )


@events.quitting.add_listener
def _enforce_thresholds(environment, **_kw) -> None:  # type: ignore[no-untyped-def]
    if isinstance(environment.runner, WorkerRunner) or environment.process_exit_code == 2:
        return
    problems = evaluate(environment.stats)
    if paced_skips:
        log.info("Tasks skipped to stay under per-account endpoint budgets: %s", paced_skips)
    if problems:
        log.error("Load test FAILED thresholds: %s", "; ".join(problems))
        environment.process_exit_code = 1
    else:
        log.info("Load test passed thresholds (%d requests)", environment.stats.total.num_requests)
        environment.process_exit_code = 0
