"""Planner forecast overrides (applied to decisions, scored against actuals) and the overstock report."""

from __future__ import annotations

from datetime import date, timedelta

from app.db.models import ForecastRun, Product, SalesRecord
from app.services import inventory_service, override_service

D0 = date(2026, 6, 1)


def _product(db, org_id, sku="A", **kw):
    p = Product(org_id=org_id, sku=sku, name=sku, **kw)
    db.add(p)
    db.flush()
    return p


def _sales(db, org_id, pid, start, values):
    for i, q in enumerate(values):
        db.add(SalesRecord(org_id=org_id, product_id=pid, sale_date=start + timedelta(days=i), quantity=q))


def _run(db, org_id, pid, as_of, yhats):
    points = [{"date": (as_of + timedelta(days=i + 1)).isoformat(), "yhat": y} for i, y in enumerate(yhats)]
    db.add(
        ForecastRun(
            org_id=org_id,
            product_id=pid,
            method="holt_winters",
            horizon=len(yhats),
            coverage=0.9,
            payload={"as_of": as_of.isoformat(), "points": points},
        )
    )


def _override(client, org, pid, start, end, kind="scale", value=2.0, reason="Promo", role="analyst"):
    return client.post(
        "/forecast-overrides",
        json={
            "product_id": str(pid),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "kind": kind,
            "value": value,
            "reason": reason,
        },
        headers=org.h(role),
    )


# --- overrides --------------------------------------------------------------------------------------------------------


def test_adjust_points_set_and_scale(db, org):
    p = _product(db, org.org_id)
    db.commit()
    override_service.create(
        db,
        org.org_id,
        org.users["analyst"],
        product_id=p.id,
        start_date=D0,
        end_date=D0,
        kind="set",
        value=7,
        reason="r",
    )
    override_service.create(
        db,
        org.org_id,
        org.users["analyst"],
        product_id=p.id,
        start_date=D0 + timedelta(days=2),
        end_date=D0 + timedelta(days=3),
        kind="scale",
        value=1.5,
        reason="s",
    )
    points = [
        {"date": (D0 + timedelta(days=i)).isoformat(), "yhat": 10.0, "lower": 8.0, "upper": 12.0} for i in range(5)
    ]
    out, applied = override_service.adjust_points(db, org.org_id, p.id, points)
    assert [x["yhat"] for x in out] == [7.0, 10.0, 15.0, 15.0, 10.0]
    assert out[0]["yhat_model"] == 10.0 and out[0]["lower"] is None
    assert out[1] == points[1]  # untouched days keep their interval
    assert sorted(a["days"] for a in applied) == [1, 2]


def test_override_validation_overlap_archive_and_permissions(client, org, db):
    p = _product(db, org.org_id)
    db.commit()
    assert _override(client, org, p.id, D0, D0 + timedelta(days=6), role="operator").status_code == 403
    assert _override(client, org, p.id, D0 + timedelta(days=1), D0).status_code == 422  # end before start
    assert _override(client, org, p.id, D0, D0 + timedelta(days=400)).status_code == 422  # too long
    assert _override(client, org, p.id, D0, D0, value=11).status_code == 422  # scale above 10
    assert _override(client, org, p.id, D0, D0, reason="  ").status_code == 422
    assert _override(client, org, p.id, D0, D0, kind="add").status_code == 422

    first = _override(client, org, p.id, D0, D0 + timedelta(days=6))
    assert first.status_code == 201, first.text
    clash = _override(client, org, p.id, D0 + timedelta(days=6), D0 + timedelta(days=8))
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "override_overlap"

    oid = first.json()["id"]
    assert client.post(f"/forecast-overrides/{oid}/archive", headers=org.h("analyst")).status_code == 200
    assert _override(client, org, p.id, D0 + timedelta(days=6), D0 + timedelta(days=8)).status_code == 201
    listed = client.get(f"/forecast-overrides?product_id={p.id}", headers=org.h("viewer")).json()
    assert listed["total"] == 1
    assert client.get("/forecast-overrides?include_archived=true", headers=org.h("viewer")).json()["total"] == 2


def test_overrides_are_tenant_scoped(client, org, other_org, db):
    p = _product(db, org.org_id)
    db.commit()
    oid = _override(client, org, p.id, D0, D0).json()["id"]
    assert _override(client, other_org, p.id, D0, D0).status_code == 404
    assert client.post(f"/forecast-overrides/{oid}/archive", headers=other_org.h()).status_code == 404
    assert client.get("/forecast-overrides", headers=other_org.h()).json()["total"] == 0
    assert client.get("/forecast-overrides/value-added", headers=other_org.h()).json()["overrides"] == []


def test_recommendation_uses_the_override_and_says_so(client, org, db):
    p = _product(db, org.org_id, on_hand=50, inventory_known=True, lead_time_days=7)
    start = date(2026, 1, 1)
    _sales(db, org.org_id, p.id, start, [10.0] * 120)
    db.commit()
    base = inventory_service.recommendation(db, org.org_id, p.id, fast=True)
    db.commit()  # it stores the forecast; end the transaction so the API can lock the product
    last = start + timedelta(days=119)
    _override(client, org, p.id, last + timedelta(days=1), last + timedelta(days=60), kind="set", value=40.0)
    db.expire_all()
    adj = inventory_service.recommendation(db, org.org_id, p.id, fast=True)
    assert adj["mean_daily_demand"] > base["mean_daily_demand"] * 3
    assert adj["recommended_order_qty"] > base["recommended_order_qty"]
    assert adj["overrides_applied"] and any("Planner override" in a for a in adj["assumptions"])

    fc = client.post("/forecast", json={"product_id": str(p.id), "horizon": 14}, headers=org.h("viewer")).json()
    assert fc["overrides"][0]["kind"] == "set"
    assert all(pt["yhat"] == 40.0 for pt in fc["adjusted_points"])
    assert all(pt["yhat"] != 40.0 for pt in fc["points"])  # the model forecast itself is unchanged


def test_value_added_scores_overrides_against_actuals(client, org, db):
    good, bad = _product(db, org.org_id, "G"), _product(db, org.org_id, "B")
    for p in (good, bad):
        _sales(db, org.org_id, p.id, D0, [20.0] * 10)
        _run(db, org.org_id, p.id, D0 - timedelta(days=1), [10.0] * 10)  # model says 10, actual is 20
    future = _product(db, org.org_id, "F")
    _sales(db, org.org_id, future.id, D0, [5.0])
    db.commit()
    _override(client, org, good.id, D0, D0 + timedelta(days=9), kind="set", value=19.0)  # close to actual
    _override(client, org, bad.id, D0, D0 + timedelta(days=9), kind="scale", value=0.5)  # further away
    _override(client, org, future.id, D0 + timedelta(days=30), D0 + timedelta(days=40))  # no actuals yet

    out = client.get("/forecast-overrides/value-added", headers=org.h("viewer")).json()
    rows = {r["sku"]: r for r in out["overrides"]}
    assert rows["G"]["scored_days"] == 10 and rows["G"]["model_wape"] == 50.0 and rows["G"]["override_wape"] == 5.0
    assert rows["G"]["fva_points"] == 45.0 and rows["G"]["verdict"] == "helped"
    assert rows["B"]["override_wape"] == 75.0 and rows["B"]["verdict"] == "hurt"
    assert rows["F"]["verdict"] == "pending" and rows["F"]["scored_days"] == 0
    assert out["overall"]["scored_days"] == 20 and out["overall"]["model_wape"] == 50.0
    assert out["overall"]["override_wape"] == 40.0 and out["overall"]["verdict"] == "helped"


# --- overstock --------------------------------------------------------------------------------------------------------


def test_overstock_report_flags_dead_and_excess_stock(client, org, db):
    as_of = date(2026, 6, 30)
    first = as_of - timedelta(days=89)
    fast = _product(db, org.org_id, "FAST", on_hand=100, inventory_known=True, unit_cost=1.0)
    slow = _product(db, org.org_id, "SLOW", on_hand=1000, on_order=50, inventory_known=True, unit_cost=2.0)
    dead = _product(db, org.org_id, "DEAD", on_hand=30, inventory_known=True)
    unknown = _product(db, org.org_id, "UNK", on_hand=9999)  # inventory not known: left out
    _sales(db, org.org_id, fast.id, first, [10.0] * 90)  # 10 days of cover
    _sales(db, org.org_id, slow.id, first, [1.0] * 90)  # 1000 days of cover
    _sales(db, org.org_id, dead.id, first - timedelta(days=200), [3.0] * 10)  # last sold ~280 days ago
    _sales(db, org.org_id, unknown.id, first, [0.0] * 90)
    db.commit()

    r = client.get("/inventory/overstock", headers=org.h("viewer"))
    assert r.status_code == 200
    out = r.json()
    assert out["as_of"] == as_of.isoformat()
    by = {i["sku"]: i for i in out["items"]}
    assert set(by) == {"SLOW", "DEAD"}
    s = by["SLOW"]
    assert s["status"] == "overstock" and s["days_of_cover"] == 1000.0 and s["avg_daily_demand"] == 1.0
    assert s["excess_units"] == 820 and s["excess_value"] == 1640.0  # 1000 - 180 days x 1/day
    assert any("on order" in t for t in s["suggestions"])
    d = by["DEAD"]
    assert d["status"] == "dead" and d["days_since_last_sale"] == 280 and d["excess_units"] == 30
    assert d["excess_value"] is None and any("unit cost" in t for t in d["suggestions"])
    assert out["items"][0]["sku"] == "SLOW"  # sorted by value tied up
    assert out["summary"] == {
        "products": 2,
        "dead": 1,
        "overstock": 1,
        "excess_value": 1640.0,
        "stock_value": 2000.0,
        "missing_cost": 1,
    }

    # FAST has only 10 days of cover, so even a 30-day target leaves it out.
    tight = client.get("/inventory/overstock?cover_days=30", headers=org.h()).json()
    assert tight["total"] == 2
    assert client.get("/inventory/overstock?cover_days=5", headers=org.h()).status_code == 422
    assert client.get("/inventory/overstock", headers=org.h()).json()["total"] == 2


def test_overstock_is_empty_without_sales_and_tenant_scoped(client, org, other_org, db):
    assert client.get("/inventory/overstock", headers=org.h()).json()["items"] == []
    p = _product(db, org.org_id, on_hand=10, inventory_known=True)
    _sales(db, org.org_id, p.id, D0 - timedelta(days=200), [1.0])
    _sales(db, org.org_id, p.id, D0, [0.0])
    db.commit()
    assert client.get("/inventory/overstock", headers=org.h()).json()["total"] == 1
    assert client.get("/inventory/overstock", headers=other_org.h()).json()["total"] == 0
