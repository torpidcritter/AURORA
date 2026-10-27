"""Standard-library helpers for locustfile.py: account parsing, per-account pacing and pass/fail thresholds.

Kept free of Locust imports so tests/unit/test_load_support.py can test them without installing Locust (which
monkey-patches the standard library with gevent on import, something a pytest process must never do).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

# Per-account limits the API enforces: app/core/config.py (api, login) and app/api/routes/forecasting.py (endpoints).
DEFAULT_API_RPM = 600
DEFAULT_LOGIN_RPM = 10
ENDPOINT_LIMITS_PER_MINUTE = {"kpi": 30, "forecast": 60, "risk-batch": 20}

# Default gates for a small local or staging stack, cold caches included. Tighten them with LOAD_P95_MS for a
# warm-cache staging run (see README.md). Keys are "METHOD name" as Locust reports them.
DEFAULT_MAX_FAIL_RATIO = 0.01
DEFAULT_MAX_P95_MS = 5000.0  # requests without their own limit, sign-ins excluded (see in_overall_gate)
DEFAULT_ENDPOINT_P95_MS = {
    "GET /products": 1000.0,
    "GET /alerts": 1000.0,
    "GET /forecast/latest": 1000.0,
    "GET /intelligence/kpi": 5000.0,
    "POST /forecast": 15000.0,
    "POST /risk/stockout/batch": 15000.0,
}
LOGIN_NAME_PREFIX = "POST /auth/login"


def in_overall_gate(name: str, endpoint_p95_ms: Mapping[str, float]) -> bool:
    """Whether a request counts towards the overall p95 gate.

    Each request is judged by one latency gate: an endpoint with its own limit only by that limit, sign-ins by none
    (bcrypt is deliberately slow), and everything else by the overall limit. Otherwise a couple of slow but in-budget
    requests to a heavy endpoint, such as cold-cache forecasts, set the overall p95 of a short run on their own.
    """
    return not name.startswith(LOGIN_NAME_PREFIX) and name not in endpoint_p95_ms


def parse_accounts(users: str | None, email: str | None, password: str | None) -> list[tuple[str, str]]:
    """LOAD_USERS ("email:password,email:password") wins over LOAD_EMAIL / LOAD_PASSWORD."""
    accounts = []
    for item in (users or "").split(","):
        e, sep, p = item.strip().partition(":")
        if sep and e and p:
            accounts.append((e, p))
    if not accounts and email and password:
        accounts.append((email, password))
    return accounts


def parse_p95_limits(raw: str | None) -> dict[str, float]:
    """LOAD_P95_MS="POST /forecast=3000,GET /products=300" -> {"POST /forecast": 3000.0, ...}."""
    limits = {}
    for item in (raw or "").split(","):
        name, sep, value = item.rpartition("=")
        if sep and name.strip():
            limits[name.strip()] = float(value)
    return limits


class Pacer:
    """Token bucket shared by every simulated user of one account.

    Refilled at a fraction (LOAD_PACING, 90% by default) of the server's per-minute limit with a small burst, it keeps
    the API's two-fixed-window estimate (current + previous window weighted by the time left) under the limit: at
    most rate * 60 + 2 * burst requests are counted. Under Locust, time.sleep and threading.Lock are gevent-patched,
    so waiting yields to the other simulated users.
    """

    def __init__(
        self,
        per_minute: float,
        burst: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate = max(per_minute, 0.1) / 60.0
        self.burst = max(burst, 1.0)
        self.tokens = self.burst
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self.tokens = min(self.burst, self.tokens + (now - self._updated) * self.rate)
        self._updated = now

    def try_take(self) -> bool:
        with self._lock:
            self._refill()
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False

    def take(self) -> None:
        """Wait until a token is available, then take it."""
        while not self.try_take():
            self._sleep(max((1 - self.tokens) / self.rate, 0.05))


@dataclass(frozen=True)
class EndpointResult:
    requests: int
    failures: int
    p95_ms: float


def check_thresholds(
    results: Mapping[str, EndpointResult],
    overall: EndpointResult,
    overall_ungated: EndpointResult,
    max_fail_ratio: float = DEFAULT_MAX_FAIL_RATIO,
    max_p95_ms: float = DEFAULT_MAX_P95_MS,
    endpoint_p95_ms: Mapping[str, float] | None = None,
) -> list[str]:
    """Return the violated thresholds of a finished run (an empty list means it passed).

    The failure ratio counts every request, sign-ins included. Latency gates skip endpoints with no requests.
    """
    if overall.requests == 0:
        return ["no requests were made"]
    problems = []
    ratio = overall.failures / overall.requests
    if ratio > max_fail_ratio:
        problems.append(f"failure ratio {ratio:.2%} > {max_fail_ratio:.2%} ({overall.failures} failed)")
    if overall_ungated.requests and overall_ungated.p95_ms > max_p95_ms:
        problems.append(
            f"p95 of requests without their own limit {overall_ungated.p95_ms:.0f} ms > {max_p95_ms:.0f} ms"
        )
    limits = DEFAULT_ENDPOINT_P95_MS if endpoint_p95_ms is None else endpoint_p95_ms
    for name, limit in limits.items():
        r = results.get(name)
        if r is not None and r.requests and r.p95_ms > limit:
            problems.append(f"{name} p95 {r.p95_ms:.0f} ms > {limit:.0f} ms")
    return problems
