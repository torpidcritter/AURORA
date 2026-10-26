"""The load test's pacing, account parsing and pass/fail rules (tests/load/load_support.py), without Locust."""

from __future__ import annotations

import pytest

from tests.load.load_support import (
    DEFAULT_ENDPOINT_P95_MS,
    EndpointResult,
    Pacer,
    check_thresholds,
    parse_accounts,
    parse_p95_limits,
)


def test_parse_accounts_prefers_load_users_and_skips_malformed_entries() -> None:
    assert parse_accounts("a@x.io:pw1, bad-entry ,b@x.io:p:w2", "c@x.io", "pw3") == [
        ("a@x.io", "pw1"),
        ("b@x.io", "p:w2"),  # only the first colon separates, so passwords may contain colons
    ]
    assert parse_accounts("", "c@x.io", "pw3") == [("c@x.io", "pw3")]
    assert parse_accounts(None, "c@x.io", None) == []


def test_parse_p95_limits() -> None:
    assert parse_p95_limits("POST /forecast=3000, GET /products=250.5,junk") == {
        "POST /forecast": 3000.0,
        "GET /products": 250.5,
    }
    assert parse_p95_limits(None) == {}
    with pytest.raises(ValueError):
        parse_p95_limits("GET /products=fast")


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_pacer_allows_burst_then_refills_at_rate() -> None:
    clock = FakeClock()
    p = Pacer(per_minute=60, burst=2, clock=clock, sleep=clock.sleep)  # one token per second
    assert p.try_take() and p.try_take()
    assert not p.try_take()
    clock.now += 0.5
    assert not p.try_take()
    clock.now += 0.5
    assert p.try_take()


def test_pacer_take_waits_instead_of_exceeding_the_rate() -> None:
    clock = FakeClock()
    p = Pacer(per_minute=30, clock=clock, sleep=clock.sleep)  # one token per two seconds, burst 1
    for _ in range(4):
        p.take()
    # The first token is free; three more need about six seconds at 0.5 tokens/s.
    assert 5.9 <= clock.now <= 6.5
    assert clock.slept and all(s > 0 for s in clock.slept)


def test_pacer_stays_under_the_server_limit_over_two_windows() -> None:
    clock = FakeClock()
    limit = 60
    p = Pacer(per_minute=limit * 0.9, burst=2, clock=clock, sleep=clock.sleep)
    taken = 0
    while clock.now < 120:
        p.take()
        taken += 1
    assert taken <= 2 * limit * 0.9 + 2 * 2


def _r(requests: int, failures: int = 0, p95: float = 100.0) -> EndpointResult:
    return EndpointResult(requests, failures, p95)


def test_thresholds_pass_and_fail() -> None:
    ok = {"GET /products": _r(100, p95=200)}
    assert check_thresholds(ok, _r(105), _r(100)) == []

    assert check_thresholds({}, _r(0), _r(0)) == ["no requests were made"]

    failing = check_thresholds(ok, _r(100, failures=5), _r(95))
    assert len(failing) == 1 and failing[0].startswith("failure ratio 5.00%")

    slow_overall = check_thresholds(ok, _r(100), _r(95, p95=9000), max_p95_ms=5000)
    assert slow_overall == ["p95 of all requests except sign-in 9000 ms > 5000 ms"]

    slow_endpoint = {"GET /products": _r(10, p95=DEFAULT_ENDPOINT_P95_MS["GET /products"] + 1)}
    assert check_thresholds(slow_endpoint, _r(10), _r(10))[0].startswith("GET /products p95")


def test_custom_endpoint_limits_replace_defaults_and_skip_unused_endpoints() -> None:
    results = {"GET /products": _r(10, p95=1500), "POST /forecast": _r(0, p95=0)}
    # Defaults would fail GET /products (limit 1000); a custom map without it does not.
    assert check_thresholds(results, _r(10), _r(10), endpoint_p95_ms={"POST /forecast": 1.0}) == []
