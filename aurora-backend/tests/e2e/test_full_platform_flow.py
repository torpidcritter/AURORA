"""Full platform flow through the real API (spec 10.5: `pytest tests/e2e/test_full_platform_flow.py -v`).

One tenant goes from an empty organisation with a single admin to trained, promoted, monitored and audited, the way
a customer would use it: the admin creates the team, an operator uploads synthetic sales, an analyst trains a small
model, everyone reads forecasts and risk, monitoring raises alerts that an operator acknowledges, and the assistant
answers in its built-in mode (no ANTHROPIC_API_KEY). Viewer (and other under-privileged) requests are refused along
the way, a second tenant sees none of it, and the sensitive steps are in the audit log.

Uses the fixtures in tests/conftest.py: the app in a TestClient, eager Celery (jobs finish inside the request),
fakeredis and synthetic data only. Kept small (3 products x 120 days, 1 training epoch) so it runs in seconds.
"""

from __future__ import annotations

import io
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import Organization
from app.devtools.synthetic import generate_sales, to_csv_bytes
from app.services import auth_service
from tests.conftest import PASSWORD, OrgFixture

ADMIN_EMAIL = "admin@e2e-retail.example.com"
TEAM = {
    "analyst": "analyst@e2e-retail.example.com",
    "operator": "operator@e2e-retail.example.com",
    "viewer": "viewer@e2e-retail.example.com",
}
# Two rows the quality report must reject, appended to otherwise clean synthetic sales.
BAD_ROWS = b"not-a-date,SKU-001,4,Demo product 1,Beverages\n2025-06-29,SKU-002,-3,Demo product 2,Snacks\n"


def _login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"] and body["refresh_token"]
    return {"Authorization": f"Bearer {body['access_token']}"}


def _upload(client: TestClient, h: dict[str, str], data: bytes, name: str = "sales.csv"):  # type: ignore[no-untyped-def]
    return client.post("/upload-dataset", files={"file": (name, io.BytesIO(data), "text/csv")}, headers=h)


@pytest.mark.slow
def test_full_platform_flow(client: TestClient, db: Session, other_org: OrgFixture) -> None:
    started = time.monotonic()

    # --- 1. Tenant bootstrap: one admin, as `python -m app.cli bootstrap` creates it ---
    org = Organization(name="E2E Retail")
    db.add(org)
    db.flush()
    auth_service.create_user(db, org_id=org.id, email=ADMIN_EMAIL, password=PASSWORD, role="admin")
    db.commit()
    admin = _login(client, ADMIN_EMAIL)
    me = client.get("/auth/me", headers=admin).json()
    assert me["role"] == "admin" and me["org_name"] == "E2E Retail" and "manage_users" in me["permissions"]

    # --- 2. The admin creates the team; everyone signs in ---
    assert (
        client.post("/auth/register", json={"email": "x@e2e-retail.example.com", "password": PASSWORD}).status_code
        == 401
    )
    weak = client.post(
        "/auth/register",
        json={"email": "weak@e2e-retail.example.com", "password": "short", "role": "viewer"},
        headers=admin,
    )
    assert weak.status_code == 422
    for role, email in TEAM.items():
        r = client.post(
            "/auth/register",
            json={"email": email, "password": PASSWORD, "role": role, "full_name": role},
            headers=admin,
        )
        assert r.status_code == 201, r.text
        assert r.json()["role"] == role and "password" not in r.text and "hash" not in r.text
    dup = client.post("/auth/register", json={"email": TEAM["viewer"], "password": PASSWORD}, headers=admin)
    assert dup.status_code == 409
    h = {role: _login(client, email) for role, email in TEAM.items()} | {"admin": admin}
    viewer, operator, analyst = h["viewer"], h["operator"], h["analyst"]
    assert client.get("/auth/me", headers=viewer).json()["permissions"] == ["read"]
    # Under-privileged users cannot manage users, whatever the frontend shows.
    for role in ("viewer", "operator", "analyst"):
        r = client.post(
            "/auth/register",
            json={"email": f"sneaky-{role}@e2e-retail.example.com", "password": PASSWORD, "role": "admin"},
            headers=h[role],
        )
        assert r.status_code == 403, role
        assert client.get("/users", headers=h[role]).status_code == 403
    assert client.get("/users", headers=admin).json()["total"] == 4

    # --- 3. Synthetic sales upload and the data-quality report ---
    sales = to_csv_bytes(generate_sales(n_products=3, days=120, intermittent=False)) + BAD_ROWS
    assert _upload(client, viewer, sales).status_code == 403
    assert _upload(client, operator, b"MZ\x90\x00", name="evil.exe").status_code == 422
    up = _upload(client, operator, sales)
    assert up.status_code == 201, up.text
    ds = up.json()["dataset"]
    assert ds["status"] == "imported" and up.json()["duplicate"] is False
    assert ds["accepted_rows"] == 360 and ds["rejected_rows"] == 2

    report = client.get(f"/datasets/{ds['id']}", headers=viewer).json()["quality_report"]
    assert report["error_counts"] == {"invalid_date": 1, "negative_quantity": 1}
    assert {e["code"] for e in report["error_samples"]} == {"invalid_date", "negative_quantity"}
    assert report["products"] == 3 and report["date_max"] == "2025-06-30"
    assert 90 <= report["quality_score"] < 100
    assert _upload(client, operator, sales).json()["duplicate"] is True  # same file again is idempotent

    products = client.get("/products?sort=sku", headers=viewer).json()
    assert products["total"] == 3
    prods = {p["sku"]: p for p in products["items"]}
    low, high = prods["SKU-001"], prods["SKU-002"]
    assert all(p["history_days"] == 120 and not p["inventory_known"] for p in prods.values())

    # Inventory: the operator records stock (one product nearly out, one overstocked); the viewer cannot.
    inv = {"expected_version": low["version"], "on_hand": 5, "lead_time_days": 7}
    assert client.patch(f"/products/{low['id']}/inventory", json=inv, headers=viewer).status_code == 403
    r = client.patch(f"/products/{low['id']}/inventory", json=inv, headers=operator)
    assert r.status_code == 200, r.text
    r = client.patch(
        f"/products/{high['id']}/inventory",
        json={"expected_version": high["version"], "on_hand": 50_000, "lead_time_days": 5},
        headers=operator,
    )
    assert r.status_code == 200, r.text

    # --- 4. Training a small Transformer, then the registry ---
    assert client.get("/models/registry", headers=viewer).json()["items"] == []
    for role in ("viewer", "operator"):
        assert client.post("/train", json={"max_epochs": 1}, headers=h[role]).status_code == 403
    assert client.post("/train", json={"max_epochs": 0}, headers=analyst).status_code == 422
    train = client.post(
        "/train", json={"max_epochs": 1, "auto_promote": False}, headers={**analyst, "Idempotency-Key": "e2e-train-1"}
    )
    assert train.status_code == 202, train.text
    job_id = train.json()["id"]
    status = client.get(f"/train/{job_id}/status", headers=viewer).json()
    assert status["status"] == "succeeded", status
    assert status["epoch"] == 1 and status["loss"] is not None and status["progress"] == 1.0
    assert status["result"]["version"] == "v1.0.0" and status["result"]["promoted"] is False
    again = client.post(
        "/train", json={"max_epochs": 1, "auto_promote": False}, headers={**analyst, "Idempotency-Key": "e2e-train-1"}
    )
    assert again.json()["id"] == job_id  # a retried request does not train twice

    registry = client.get("/models/registry", headers=viewer).json()["items"]
    assert len(registry) == 1 and registry[0]["status"] == "candidate" and registry[0]["job_id"] == job_id
    version = registry[0]
    detail = client.get(f"/models/registry/{version['id']}", headers=viewer).json()
    assert detail["checksum"] == version["checksum"] and detail["training"]["epochs_run"] == 1
    for role in ("viewer", "operator", "analyst"):  # promotion is admin-only
        assert client.post(f"/models/registry/{version['id']}/promote", json={}, headers=h[role]).status_code == 403
    promoted = client.post(
        f"/models/registry/{version['id']}/promote", json={"force": not version["gate_passed"]}, headers=admin
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["status"] == "active"
    statuses = [m["status"] for m in client.get("/models/registry", headers=viewer).json()["items"]]
    assert statuses == ["active"]
    diag = client.get("/intelligence/diagnostics/active", headers=viewer).json()
    assert diag["model"]["version"] == "v1.0.0" and set(diag["interval_coverage"]) == {"0.8", "0.9", "0.95"}

    # --- 5. Forecasts with intervals and explanations (read-only, so the viewer can) ---
    f = client.post("/forecast", json={"product_id": low["id"], "horizon": 14, "coverage": 0.9}, headers=viewer)
    assert f.status_code == 200, f.text
    fc = f.json()
    assert len(fc["points"]) == 14 and fc["as_of"] == "2025-06-30" and fc["points"][0]["date"] == "2025-07-01"
    assert all(0 <= p["lower"] <= p["yhat"] <= p["upper"] for p in fc["points"])
    assert fc["interval"]["calibrated"] is True and fc["backtest"]["best_method"]
    tf = client.post("/forecast", json={"product_id": low["id"], "method": "transformer", "horizon": 7}, headers=viewer)
    assert tf.status_code == 200, tf.text
    assert tf.json()["model_version"] == "v1.0.0" and len(tf.json()["points"]) == 7
    assert all(p["lower"] <= p["yhat"] <= p["upper"] for p in tf.json()["points"])
    assert client.post("/forecast", json={"product_id": low["id"], "horizon": 0}, headers=viewer).status_code == 422
    latest = client.get("/forecast/latest", headers=viewer).json()["items"]
    assert [i["sku"] for i in latest] == ["SKU-001"]

    ex = client.get(f"/forecast/explain/{low['id']}", headers=viewer).json()
    assert ex["sku"] == "SKU-001" and {"weekend_effect", "trend"} <= {d["driver"] for d in ex["drivers"]}
    assert ex["model_feature_importance"] is not None  # the active model's permutation importance

    # --- 6. Inventory recommendation, stockout risk and a scenario ---
    rec = client.post("/inventory/recommendation", json={"product_id": low["id"]}, headers=viewer).json()
    assert rec["status"] == "ok" and rec["on_hand"] == 5 and rec["lead_time_days"] == 7
    assert rec["recommended_order_qty"] > 0 and rec["reorder_point"] > rec["on_hand"]
    assert rec["risk_level"] in ("critical", "high")
    risk = client.post("/risk/stockout", json={"product_id": low["id"]}, headers=viewer).json()
    assert risk["risk_level"] == rec["risk_level"] and risk["days_until_stockout"] is not None
    batch = client.post("/risk/stockout/batch", json={}, headers=viewer).json()
    levels = {r["sku"]: r["risk_level"] for r in batch["items"]}
    assert batch["total"] == 3 and levels["SKU-003"] == "unknown" and levels["SKU-002"] not in ("critical", "high")

    sc = client.post(
        "/simulate/scenario",
        json={"product_id": low["id"], "adjustments": [{"type": "demand_increase", "params": {"pct": 50}}]},
        headers=viewer,
    )
    assert sc.status_code == 200, sc.text
    assert sc.json()["scenario"]["reorder_point"] > sc.json()["baseline"]["reorder_point"]
    bad = client.post(
        "/simulate/scenario", json={"product_id": low["id"], "adjustments": [{"type": "rm -rf"}]}, headers=viewer
    )
    assert bad.status_code == 422

    # --- 7. Monitoring raises alerts; an operator acknowledges one ---
    for role in ("viewer", "operator"):
        assert client.post("/jobs/monitor", headers=h[role]).status_code == 403
    mon = client.post("/jobs/monitor", headers=analyst)
    assert mon.status_code == 202, mon.text
    job = client.get(f"/jobs/{mon.json()['id']}", headers=viewer).json()
    assert job["status"] == "succeeded" and job["result"]["stockout_alerts"] >= 1, job

    alerts = client.get("/alerts?status=active&kind=stockout_risk", headers=viewer).json()["items"]
    assert [a["product_id"] for a in alerts] == [low["id"]]
    alert = alerts[0]
    assert alert["status"] == "open" and alert["severity"] in ("critical", "high")
    assert client.get("/alerts/summary", headers=viewer).json()["open"] >= 1
    assert client.post(f"/alerts/{alert['id']}/acknowledge", headers=viewer).status_code == 403
    ack = client.post(f"/alerts/{alert['id']}/acknowledge", headers=operator)
    assert ack.status_code == 200, ack.text
    assert ack.json()["status"] == "acknowledged" and ack.json()["acknowledged_by"] == str(
        client.get("/auth/me", headers=operator).json()["id"]
    )
    assert client.delete(f"/alerts/{alert['id']}", headers=operator).status_code == 403

    # --- 8. The assistant answers from real data in built-in (fallback) mode ---
    chat = client.post("/chat", json={"message": "Which products are at risk of a stockout?"}, headers=viewer)
    assert chat.status_code == 200, chat.text
    answer = chat.json()
    assert answer["mode"] == "fallback" and "not configured" in answer["notice"]
    assert "SKU-001" in answer["answer"] and answer["tool_calls"] == [{"tool": "get_risk_summary", "ok": True}]
    assert client.post("/chat", json={"message": ""}, headers=viewer).status_code == 422

    # --- 9. The audit trail of the sensitive steps ---
    for role in ("viewer", "operator"):
        assert client.get("/audit", headers=h[role]).status_code == 403
    events = client.get("/audit?limit=200", headers=analyst).json()["items"]
    by_action: dict[str, list[dict]] = {}
    for e in events:
        by_action.setdefault(e["action"], []).append(e)
    expected_actors = {
        "user.create": ADMIN_EMAIL,
        "dataset.upload": TEAM["operator"],
        "inventory.update": TEAM["operator"],
        "model.train_requested": TEAM["analyst"],
        "model.promote": ADMIN_EMAIL,
        "alert.acknowledge": TEAM["operator"],
        "assistant.chat": TEAM["viewer"],
    }
    for action, actor in expected_actors.items():
        assert action in by_action, f"missing audit event {action}; have {sorted(by_action)}"
        assert {e["actor_email"] for e in by_action[action]} == {actor}, action
    assert len(by_action["user.create"]) == 3 and len(by_action["inventory.update"]) == 2
    assert len(by_action["dataset.upload"]) == 1  # the duplicate upload is not a second import
    assert by_action["model.promote"][0]["resource_id"] == version["id"]
    assert by_action["alert.acknowledge"][0]["resource_id"] == alert["id"]
    assert {e["details"]["role"] for e in by_action["user.create"]} == set(TEAM)
    assert PASSWORD not in str(events)  # no credentials in the audit trail
    assert "Which products" not in str(by_action["assistant.chat"])  # nor the assistant question
    logins = {e["actor_email"] for e in by_action["auth.login"] if e["outcome"] == "success"}
    assert logins == {ADMIN_EMAIL, *TEAM.values()}  # sign-ins are recorded too

    # --- 10. Another tenant sees none of it ---
    them = other_org.h("admin")
    assert client.get("/products", headers=them).json()["total"] == 0
    assert client.get(f"/products/{low['id']}", headers=them).status_code == 404
    assert client.get(f"/datasets/{ds['id']}", headers=them).status_code == 404
    assert client.post("/forecast", json={"product_id": low["id"]}, headers=them).status_code == 404
    assert client.get(f"/jobs/{job_id}", headers=them).status_code == 404
    assert client.get(f"/models/registry/{version['id']}", headers=them).status_code == 404
    assert client.post(f"/models/registry/{version['id']}/promote", json={}, headers=them).status_code == 404
    assert client.post(f"/alerts/{alert['id']}/acknowledge", headers=them).status_code == 404
    assert client.get("/models/registry", headers=them).json()["items"] == []
    assert client.get("/alerts", headers=them).json()["total"] == 0
    their_audit = client.get("/audit?limit=200", headers=them).json()["items"]
    assert not {e["actor_email"] for e in their_audit} & {ADMIN_EMAIL, *TEAM.values()}

    assert time.monotonic() - started < 60, "the end-to-end flow should stay fast enough for every CI run"
