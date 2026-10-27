"""Category and organisation forecasts whose product numbers add up to the group number (ADR-035)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from app.db.models import Product, SalesRecord
from app.services import hierarchy_service

D0 = date(2026, 3, 1)
DAYS = 120


def _product(db, org_id, sku, category):
    p = Product(org_id=org_id, sku=sku, name=f"{sku} name", category=category)
    db.add(p)
    db.flush()
    return p


def _sales(db, org_id, pid, values, start=D0):
    for i, q in enumerate(values):
        db.add(SalesRecord(org_id=org_id, product_id=pid, sale_date=start + timedelta(days=i), quantity=float(q)))


def _weekly(level, n=DAYS, seed=0):
    rng = np.random.default_rng(seed)
    week = np.array([1.0, 0.9, 0.9, 1.0, 1.2, 1.5, 1.3])
    return np.round(level * week[np.arange(n) % 7] + rng.normal(0, level * 0.05, n)).clip(0)


def _seed(db, org_id):
    a = _product(db, org_id, "TEA-1", "Tea")
    b = _product(db, org_id, "TEA-2", "Tea")
    c = _product(db, org_id, "MUG-1", "Mugs")
    d = _product(db, org_id, "LOOSE", None)
    new = _product(db, org_id, "TEA-NEW", "Tea")  # no sales yet
    _sales(db, org_id, a.id, _weekly(20, seed=1))
    _sales(db, org_id, b.id, _weekly(8, seed=2))
    _sales(db, org_id, c.id, _weekly(5, seed=3))
    _sales(db, org_id, d.id, _weekly(3, seed=4))
    db.commit()
    return a, b, c, d, new


def _post(client, org, role="viewer", **body):
    return client.post("/forecast/hierarchy", json=body, headers=org.h(role))


def test_categories_are_listed_with_recent_units(client, org, db):
    _seed(db, org.org_id)
    rows = client.get("/forecast/hierarchy/categories", headers=org.h("viewer")).json()
    assert [r["label"] for r in rows] == ["Tea", "Mugs", "Uncategorised"]
    tea = rows[0]
    assert tea["products"] == 3 and tea["units_30d"] > 0
    assert rows[2]["category"] is None


@pytest.mark.parametrize("reconcile", ["bottom_up", "top_down"])
def test_products_add_up_to_the_category(client, org, db, reconcile):
    a, b, *_ = _seed(db, org.org_id)
    r = _post(client, org, level="category", category="Tea", horizon=14, reconcile=reconcile)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["label"] == "Tea" and out["products"] == 3 and out["products_forecast"] == 2
    assert out["excluded"] == [{"product_id": str(_[2].id), "sku": "TEA-NEW", "reason": "no sales history"}]
    assert out["reconcile"] == reconcile and len(out["points"]) == 14
    assert out["points"][0]["date"] == (D0 + timedelta(days=DAYS)).isoformat()
    group = sum(p["yhat"] for p in out["points"])
    assert sum(b["units"] for b in out["breakdown"]) == pytest.approx(group, rel=1e-3)
    assert {b["sku"] for b in out["breakdown"]} == {"TEA-1", "TEA-2"}
    assert out["breakdown"][0]["sku"] == "TEA-1"  # the bigger seller first
    if reconcile == "bottom_up":
        assert all(p["yhat"] == pytest.approx(p["bottom_up"], abs=1e-3) for p in out["points"])
        assert all(p["lower"] is None for p in out["points"]) and out["interval"]["calibrated"] is False
        # Bottom-up keeps each product's own forecast.
        assert all(b["units"] == pytest.approx(b["own_forecast_units"], abs=0.01) for b in out["breakdown"])
    else:
        assert all(p["yhat"] == pytest.approx(p["direct"], abs=1e-3) for p in out["points"])
        assert out["interval"]["calibrated"] is True
        assert all(p["lower"] <= p["yhat"] <= p["upper"] for p in out["points"])
    # Both methods are scored on the category's own history; the recommendation follows the lower WAPE.
    w = out["backtest"]["wape"]
    assert out["backtest"]["folds"] == 4 and w["direct"] is not None and w["bottom_up"] is not None
    assert out["recommended"] == ("top_down" if w["direct"] < w["bottom_up"] else "bottom_up")
    assert len(out["history"]) == hierarchy_service.HISTORY_DAYS


def test_total_level_and_uncategorised(client, org, db):
    _seed(db, org.org_id)
    total = _post(client, org, level="total", horizon=7, reconcile="bottom_up").json()
    assert total["label"] == "All products" and total["products_forecast"] == 4
    loose = _post(client, org, level="category", category=None, horizon=7, reconcile="bottom_up").json()
    assert loose["label"] == "Uncategorised" and [b["sku"] for b in loose["breakdown"]] == ["LOOSE"]
    # Without a choice, the better-backtesting method is used.
    auto = _post(client, org, level="total", horizon=7).json()
    assert auto["reconcile"] == auto["recommended"]


def test_overrides_flow_into_the_bottom_up_total(client, org, db):
    a, *_ = _seed(db, org.org_id)
    before = _post(client, org, level="category", category="Tea", horizon=7, reconcile="bottom_up").json()
    start = D0 + timedelta(days=DAYS)
    r = client.post(
        "/forecast-overrides",
        json={
            "product_id": str(a.id),
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=6)).isoformat(),
            "kind": "scale",
            "value": 2.0,
            "reason": "Promotion",
        },
        headers=org.h("analyst"),
    )
    assert r.status_code == 201, r.text
    after = _post(client, org, level="category", category="Tea", horizon=7, reconcile="bottom_up").json()
    tea1_before = next(b for b in before["breakdown"] if b["sku"] == "TEA-1")
    tea1_after = next(b for b in after["breakdown"] if b["sku"] == "TEA-1")
    assert tea1_after["overrides"] == 1
    assert tea1_after["units"] == pytest.approx(2 * tea1_before["units"], rel=1e-3)


def test_stale_products_and_validation(client, org, other_org, db):
    a, b, *_ = _seed(db, org.org_id)
    stale = _product(db, org.org_id, "TEA-OLD", "Tea")
    _sales(db, org.org_id, stale.id, _weekly(4, n=30), start=D0)  # stopped selling 90 days before the latest sale
    db.commit()
    out = _post(client, org, level="category", category="Tea", horizon=7, reconcile="bottom_up").json()
    old = next(b for b in out["breakdown"] if b["sku"] == "TEA-OLD")
    assert old["units"] == 0.0 and any(e["sku"] == "TEA-OLD" and "no sales for" in e["reason"] for e in out["excluded"])

    assert _post(client, org, level="category", category="Nope").status_code == 422
    assert _post(client, org, level="total", horizon=0).status_code == 422
    # Another organisation sees none of it.
    assert _post(client, other_org, level="category", category="Tea").status_code == 422
    assert client.get("/forecast/hierarchy/categories", headers=other_org.h()).json() == []


def test_group_size_is_capped(client, org, db, monkeypatch):
    _seed(db, org.org_id)
    monkeypatch.setattr(hierarchy_service, "MAX_PRODUCTS", 2)
    r = _post(client, org, level="total")
    assert r.status_code == 422 and "up to 2" in r.json()["error"]["message"]
