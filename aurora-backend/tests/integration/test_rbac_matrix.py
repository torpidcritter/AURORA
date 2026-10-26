"""Every protected route: roles below the minimum get 403, roles at or above never get 401/403,
and unauthenticated calls get 401. Also asserts that no route was forgotten."""

from __future__ import annotations

import uuid

import pytest

ROLES = ["viewer", "operator", "analyst", "admin"]
X = str(uuid.uuid4())

MATRIX = [
    ("GET", "/auth/me", None, "viewer"),
    ("POST", "/auth/register", {"email": "a@example.com", "password": "Xx-123456789", "role": "viewer"}, "admin"),
    ("POST", "/auth/password", {"current_password": "wrong", "new_password": "Xx-123456789"}, "viewer"),
    ("POST", f"/auth/users/{X}/reset-link", None, "admin"),
    ("GET", "/users", None, "admin"),
    ("PATCH", f"/users/{X}", {"full_name": "x"}, "admin"),
    ("POST", f"/users/{X}/password", {"new_password": "Xx-123456789"}, "admin"),
    ("POST", "/datasets/upload", "FILE", "analyst"),
    ("POST", "/upload-dataset", "FILE", "operator"),
    ("GET", "/datasets", None, "viewer"),
    ("GET", f"/datasets/{X}", None, "viewer"),
    ("GET", "/products", None, "viewer"),
    ("GET", "/products/categories", None, "viewer"),
    ("GET", f"/products/{X}", None, "viewer"),
    ("GET", f"/products/{X}/history", None, "viewer"),
    ("PATCH", f"/products/{X}/inventory", {"expected_version": 1, "on_hand": 1}, "operator"),
    ("POST", "/inventory/upload", "FILE", "operator"),
    ("POST", "/forecast", {"product_id": X}, "viewer"),
    ("GET", "/forecast/methods", None, "viewer"),
    ("GET", f"/forecast/explain/{X}", None, "viewer"),
    ("GET", f"/forecast/history/{X}", None, "viewer"),
    ("GET", "/forecast/latest", None, "viewer"),
    ("GET", "/intelligence/demand-trend", None, "viewer"),
    ("POST", "/inventory/recommendation", {"product_id": X}, "viewer"),
    ("POST", "/risk/stockout", {"product_id": X}, "viewer"),
    ("POST", "/risk/stockout/batch", {}, "viewer"),
    ("GET", "/intelligence/kpi", None, "viewer"),
    ("GET", "/intelligence/recommendations", None, "viewer"),
    ("POST", "/intelligence/backtest", {"product_id": X}, "viewer"),
    ("GET", "/intelligence/drift", None, "viewer"),
    ("GET", f"/intelligence/diagnostics/{X}", None, "viewer"),
    ("GET", "/simulate/types", None, "viewer"),
    ("POST", "/simulate/scenario", {"product_id": X, "adjustments": [{"type": "demand_increase"}]}, "viewer"),
    ("GET", "/signals", None, "viewer"),
    (
        "POST",
        "/signals",
        {"name": "n", "signal_type": "holiday", "start_date": "2025-01-01", "end_date": "2025-01-01", "magnitude": 0.1},
        "analyst",
    ),
    ("POST", "/signals/upload", "FILE", "analyst"),
    ("DELETE", f"/signals/{X}", None, "analyst"),
    ("POST", "/train", {"max_epochs": 1}, "analyst"),
    ("GET", f"/train/{X}/status", None, "viewer"),
    ("POST", "/jobs/batch-forecast", None, "analyst"),
    ("POST", "/jobs/monitor", None, "analyst"),
    ("GET", "/jobs", None, "viewer"),
    ("GET", f"/jobs/{X}", None, "viewer"),
    ("POST", f"/jobs/{X}/cancel", None, "analyst"),
    ("GET", "/models/registry", None, "viewer"),
    ("GET", f"/models/registry/{X}", None, "viewer"),
    ("POST", f"/models/registry/{X}/promote", {}, "admin"),
    ("POST", "/models/registry/rollback", None, "admin"),
    ("GET", "/alerts", None, "viewer"),
    ("GET", "/alerts/summary", None, "viewer"),
    ("POST", f"/alerts/{X}/acknowledge", None, "operator"),
    ("POST", f"/alerts/{X}/resolve", None, "operator"),
    ("DELETE", f"/alerts/{X}", None, "admin"),
    ("GET", "/audit", None, "analyst"),
    ("GET", "/ops/status", None, "analyst"),
]

PUBLIC = {
    "/health/live",
    "/health/ready",
    "/health",
    "/metrics",
    "/auth/login",
    "/auth/refresh",
    "/auth/logout",
    "/auth/password-reset",
    "/docs",
    "/openapi.json",
    "/docs/oauth2-redirect",
}
EXTRA_PREFIXES = ("/shopify", "/chat")  # covered by their own test modules


def call(client, method, path, body, headers):
    if body == "FILE":
        return client.request(method, path, files={"file": ("x.csv", b"date,sku,qty\n", "text/csv")}, headers=headers)
    return client.request(method, path, json=body, headers=headers)


@pytest.mark.parametrize("method,path,body,min_role", MATRIX, ids=[f"{m} {p}" for m, p, _, _ in MATRIX])
def test_permission_matrix(client, org, method, path, body, min_role):
    assert call(client, method, path, body, {}).status_code == 401
    for role in ROLES:
        r = call(client, method, path, body, org.h(role))
        if ROLES.index(role) < ROLES.index(min_role):
            assert r.status_code == 403, f"{role} should be denied {method} {path}: {r.status_code}"
        else:
            assert r.status_code not in (401, 403), f"{role} should be allowed {method} {path}: {r.text[:200]}"


def test_every_route_is_in_the_matrix(app):
    covered = {(m, p.replace(X, "{id}")) for m, p, _, _ in MATRIX}
    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if path in PUBLIC or path.startswith(EXTRA_PREFIXES):
            continue
        norm = path
        for param in (
            "{user_id}",
            "{dataset_id}",
            "{product_id}",
            "{signal_id}",
            "{job_id}",
            "{version_id}",
            "{alert_id}",
        ):
            norm = norm.replace(param, "{id}")
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            if (method, norm) not in covered:
                missing.append(f"{method} {path}")
    assert not missing, f"routes missing from RBAC matrix: {missing}"
