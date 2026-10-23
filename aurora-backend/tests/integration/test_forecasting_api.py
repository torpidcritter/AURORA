from __future__ import annotations

import io

import pytest

from app.devtools.synthetic import generate_sales, to_csv_bytes


@pytest.fixture
def loaded(client, org):
    df = generate_sales(n_products=3, days=120)
    r = client.post(
        "/datasets/upload",
        files={"file": ("s.csv", io.BytesIO(to_csv_bytes(df)), "text/csv")},
        headers=org.h("operator"),
    )
    assert r.status_code == 201
    prods = client.get("/products", headers=org.h()).json()["items"]
    return {p["sku"]: p for p in prods}


def test_forecast_auto_with_intervals(client, org, loaded):
    pid = loaded["SKU-001"]["id"]
    r = client.post("/forecast", json={"product_id": pid, "horizon": 7}, headers=org.h("viewer"))
    assert r.status_code == 200, r.text
    f = r.json()
    assert len(f["points"]) == 7 and f["as_of"] == "2025-06-30"
    assert f["points"][0]["date"] == "2025-07-01"
    assert f["method"] in ("sma", "ses", "holt_winters", "seasonal_naive", "croston_sba")
    assert f["interval"]["calibrated"] is True
    assert all(p["lower"] <= p["yhat"] <= p["upper"] for p in f["points"])
    assert all(p["lower"] >= 0 for p in f["points"])
    assert f["transformer_status"] == "no active Transformer model"
    assert f["backtest"]["folds"] == 4 and f["backtest"]["best_method"] == f["method"]
    widths95 = client.post(
        "/forecast", json={"product_id": pid, "horizon": 7, "coverage": 0.95, "method": f["method"]}, headers=org.h()
    ).json()["points"]
    assert widths95[0]["upper"] - widths95[0]["lower"] >= f["points"][0]["upper"] - f["points"][0]["lower"]


def test_forecast_validation(client, org, loaded):
    pid = loaded["SKU-001"]["id"]
    assert client.post("/forecast", json={"product_id": pid, "horizon": 0}, headers=org.h()).status_code == 422
    assert (
        client.post("/forecast", json={"product_id": pid, "horizon": 7, "coverage": 0.5}, headers=org.h()).status_code
        == 422
    )
    assert client.post("/forecast", json={"product_id": pid, "method": "magic"}, headers=org.h()).status_code == 422
    r = client.post("/forecast", json={"product_id": pid, "method": "transformer"}, headers=org.h())
    assert r.status_code == 422 and "Transformer unavailable" in r.json()["error"]["message"]
    assert (
        client.post(
            "/forecast", json={"product_id": "00000000-0000-0000-0000-000000000000"}, headers=org.h()
        ).status_code
        == 404
    )


def test_methods_and_explain(client, org, loaded):
    m = client.get("/forecast/methods", headers=org.h("viewer")).json()
    assert {x["name"] for x in m["methods"]} >= {"transformer", "sma", "ses", "holt_winters"}
    e = client.get(f"/forecast/explain/{loaded['SKU-001']['id']}", headers=org.h("viewer")).json()
    names = {d["driver"] for d in e["drivers"]}
    assert "weekend_effect" in names and "trend" in names
    assert "SHAP" in e["method_note"]  # explicitly says it is not SHAP
    assert "festival" not in str(e["drivers"]).lower()


def test_inventory_unknown_then_known(client, org, loaded):
    p = loaded["SKU-001"]
    r = client.post("/inventory/recommendation", json={"product_id": p["id"]}, headers=org.h("viewer")).json()
    assert r["status"] == "unknown_inventory"
    client.patch(
        f"/products/{p['id']}/inventory",
        json={"expected_version": p["version"], "on_hand": 20, "lead_time_days": 5},
        headers=org.h("operator"),
    )
    r = client.post("/inventory/recommendation", json={"product_id": p["id"]}, headers=org.h("viewer")).json()
    assert r["status"] == "ok"
    assert r["lead_time_days"] == 5 and r["on_hand"] == 20
    assert r["reorder_point"] == pytest.approx(r["mean_daily_demand"] * 5 + r["safety_stock"], rel=1e-3)
    assert r["recommended_order_qty"] > 0 and r["risk_level"] in ("critical", "high")
    assert "Service level not set" in " ".join(r["assumptions"])
    assert r["sku"] in r["narrative"]
    s = client.post("/risk/stockout", json={"product_id": p["id"]}, headers=org.h("viewer")).json()
    assert s["risk_level"] == r["risk_level"]


def test_risk_batch_kpi_recommendations(client, org, loaded):
    p = loaded["SKU-002"]
    client.patch(
        f"/products/{p['id']}/inventory",
        json={"expected_version": p["version"], "on_hand": 5000},
        headers=org.h("operator"),
    )
    b = client.post("/risk/stockout/batch", json={}, headers=org.h("viewer")).json()
    assert b["total"] == 3
    levels = [r["risk_level"] for r in b["items"]]
    assert levels.count("unknown") == 2
    k = client.get("/intelligence/kpi", headers=org.h("viewer")).json()
    assert k["products_total"] == 3 and k["products_with_inventory"] == 1
    assert k["risk_counts"]["unknown"] == 2 and k["as_of"] == "2025-06-30"
    assert k["units_last_30d"] > 0 and "definitions" in k
    recs = client.get("/intelligence/recommendations", headers=org.h("viewer")).json()["items"]
    types = {r["type"] for r in recs}
    assert "data" in types and "overstock" in types


def test_backtest_endpoint(client, org, loaded):
    r = client.post(
        "/intelligence/backtest", json={"product_id": loaded["SKU-001"]["id"], "horizon": 7}, headers=org.h("viewer")
    )
    assert r.status_code == 200, r.text
    b = r.json()
    assert set(b["summary"]["methods"]) == {"sma", "ses", "holt_winters", "seasonal_naive", "croston_sba"}
    assert b["summary"]["origins"] == 4
    assert len(b["summary"]["methods"]["sma"]["per_horizon_mae"]) == 7
    assert b["interval_coverage"]["sma"]["0.9"]["n_test"] > 0


def test_scenarios(client, org, loaded):
    p = loaded["SKU-001"]
    client.patch(
        f"/products/{p['id']}/inventory",
        json={"expected_version": p["version"], "on_hand": 300},
        headers=org.h("operator"),
    )
    types = client.get("/simulate/types", headers=org.h("viewer")).json()["types"]
    assert "lead_time_increase" in types
    r = client.post(
        "/simulate/scenario",
        json={
            "product_id": p["id"],
            "adjustments": [
                {"type": "demand_increase", "params": {"pct": 50}},
                {"type": "lead_time_increase", "params": {"days": 15}},
            ],
        },
        headers=org.h("viewer"),
    )
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["scenario"]["reorder_point"] > s["baseline"]["reorder_point"]
    assert s["deltas"]["safety_stock"] > 0
    c = s["curve"][0]
    assert c["scenario"] == pytest.approx(c["baseline"] * 1.5, rel=1e-3)
    bad = client.post(
        "/simulate/scenario",
        json={"product_id": p["id"], "adjustments": [{"type": "demand_increase", "params": {"pct": 10000}}]},
        headers=org.h(),
    )
    assert bad.status_code == 422
    bad = client.post(
        "/simulate/scenario",
        json={"product_id": p["id"], "adjustments": [{"type": "rm -rf", "params": {}}]},
        headers=org.h(),
    )
    assert bad.status_code == 422
    bad = client.post(
        "/simulate/scenario",
        json={"product_id": p["id"], "adjustments": [{"type": "demand_increase", "params": {"evil": 1}}]},
        headers=org.h(),
    )
    assert bad.status_code == 422


def test_signals_crud_and_scenario(client, org, loaded):
    body = {
        "name": "Summer sale",
        "signal_type": "promotion",
        "start_date": "2025-07-02",
        "end_date": "2025-07-04",
        "magnitude": 0.5,
    }
    assert client.post("/signals", json=body, headers=org.h("operator")).status_code == 403
    r = client.post("/signals", json=body, headers=org.h("analyst"))
    assert r.status_code == 201
    assert client.post("/signals", json={**body, "magnitude": 2}, headers=org.h("analyst")).status_code == 422
    csv = (
        b"name,signal_type,start_date,end_date,magnitude,sku\n"
        b"Heat,weather,2025-07-01,2025-07-01,-0.2,SKU-001\nBad,weather,x,y,1,\n"
    )
    up = client.post(
        "/signals/upload", files={"file": ("s.csv", io.BytesIO(csv), "text/csv")}, headers=org.h("analyst")
    )
    assert up.json()["created"] == 1 and up.json()["error_count"] == 1
    assert len(client.get("/signals", headers=org.h("viewer")).json()) == 2
    p = loaded["SKU-001"]
    client.patch(
        f"/products/{p['id']}/inventory",
        json={"expected_version": p["version"], "on_hand": 300},
        headers=org.h("operator"),
    )
    f = client.post("/forecast", json={"product_id": p["id"]}, headers=org.h()).json()
    assert len(f["signals"]) == 2 and any("not applied" in w for w in f["warnings"])
    s = client.post(
        "/simulate/scenario",
        json={"product_id": p["id"], "adjustments": [{"type": "apply_signals", "params": {"sensitivity_pct": 100}}]},
        headers=org.h(),
    ).json()
    curve = s["curve"]
    assert curve[0]["scenario"] == pytest.approx(curve[0]["baseline"] * 0.8, rel=1e-3)  # weather -0.2
    assert curve[1]["scenario"] == pytest.approx(curve[1]["baseline"] * 1.5, rel=1e-3)  # promo +0.5
    sid = r.json()["id"]
    assert client.delete(f"/signals/{sid}", headers=org.h("analyst")).status_code == 204


def test_drift_endpoint(client, org, loaded):
    d = client.get("/intelligence/drift", headers=org.h("viewer")).json()
    assert len(d["items"]) == 3
    assert all("ks_statistic" in i or i["status"] == "insufficient_data" for i in d["items"])


def test_latest_forecasts_and_demand_trend(client, org, loaded):
    pid = loaded["SKU-001"]["id"]
    client.post("/forecast", json={"product_id": pid, "horizon": 7}, headers=org.h())
    client.post("/forecast", json={"product_id": pid, "horizon": 14}, headers=org.h())
    items = client.get("/forecast/latest", headers=org.h("viewer")).json()["items"]
    assert len(items) == 1 and items[0]["horizon"] == 14 and items[0]["next_7d_units"] > 0
    t = client.get("/intelligence/demand-trend?days=30", headers=org.h("viewer")).json()
    assert t["as_of"] == "2025-06-30" and len(t["points"]) == 30
    assert t["points"][-1]["date"] == "2025-06-30" and t["points"][-1]["units"] > 0
