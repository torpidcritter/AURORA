"""Promotions: measuring past ones, estimating and applying planned ones (ADR-045)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db.models import AuditEvent, Product, SalesRecord
from app.services import inventory_service, override_service, promotion_service

START = date(2026, 1, 1)
DAYS = 120
AS_OF = START + timedelta(days=DAYS - 1)


def _product(db, org_id, sku, category):
    p = Product(
        org_id=org_id,
        sku=sku,
        name=sku,
        category=category,
        unit_cost=2.0,
        lead_time_days=7,
        on_hand=50,
        inventory_known=True,
    )
    db.add(p)
    db.flush()
    return p


def _a_sales(i: int) -> float:
    if 40 <= i <= 46:
        return 20.0  # first promotion: lift 2
    if 60 <= i <= 66:
        return 30.0  # second promotion: lift 3 over a baseline that leaves out the first one
    if 67 <= i <= 80:
        return 8.0  # customers stocked up
    return 10.0


def _seed(db, org_id):
    a = _product(db, org_id, "TEA-A", "Tea")
    b = _product(db, org_id, "TEA-B", "Tea")
    c = _product(db, org_id, "MUG-C", "Mugs")
    for i in range(DAYS):
        d = START + timedelta(days=i)
        db.add(SalesRecord(org_id=org_id, product_id=a.id, sale_date=d, quantity=_a_sales(i)))
        db.add(SalesRecord(org_id=org_id, product_id=b.id, sale_date=d, quantity=5.0))
        db.add(SalesRecord(org_id=org_id, product_id=c.id, sale_date=d, quantity=3.0))
    db.commit()
    return a, b, c


def _day(i: int) -> str:
    return (START + timedelta(days=i)).isoformat()


def _past(client, org, a, *, first, last, discount, name):
    body = {"name": name, "start_date": _day(first), "end_date": _day(last), "product_ids": [str(a.id)]}
    r = client.post("/promotions", json={**body, "discount_pct": discount}, headers=org.h("analyst"))
    assert r.status_code == 201, r.text
    return r.json()


def test_measure_and_estimate(client, org, db):
    a, b, c = _seed(db, org.org_id)
    first = _past(client, org, a, first=40, last=46, discount=20, name="Spring sale")
    second = _past(client, org, a, first=60, last=66, discount=50, name="Half price week")
    assert second["status"] == "finished"
    m = second["measurement"]
    row = m["products"][0]
    assert row["baseline_per_day"] == pytest.approx(10)  # the first promotion's days are left out
    assert row["lift"] == pytest.approx(3.0) and m["lift"] == pytest.approx(3.0)
    assert row["dip"] == pytest.approx(0.8)
    assert m["incremental_units"] == pytest.approx(140)
    assert client.get(f"/promotions/{first['id']}", headers=org.h("viewer")).json()["measurement"]["lift"] == 2.0

    est = client.post("/promotions/estimate", json={"product_ids": [str(a.id)]}, headers=org.h("viewer")).json()
    assert est["uplift"] == 2.5 and est["basis"] == "the same products' past promotions"
    near = client.post(
        "/promotions/estimate", json={"product_ids": [str(a.id)], "discount_pct": 45}, headers=org.h("viewer")
    ).json()
    assert near["uplift"] == 3.0 and "similar discount" in near["basis"]
    cat = client.post("/promotions/estimate", json={"product_ids": [str(b.id)]}, headers=org.h("viewer")).json()
    assert cat["basis"] == "past promotions in the same category" and cat["uplift"] == 2.5
    other = client.post("/promotions/estimate", json={"category": "Mugs"}, headers=org.h("viewer")).json()
    assert other["basis"] == "all past promotions"


def test_planned_promotion_raises_demand_and_overrides_win(client, org, db):
    a, b, _ = _seed(db, org.org_id)
    _past(client, org, a, first=60, last=66, discount=50, name="Half price week")
    start, end = AS_OF + timedelta(days=3), AS_OF + timedelta(days=9)
    body = {"name": "Summer", "start_date": start.isoformat(), "end_date": end.isoformat(), "category": "Tea"}
    assert client.post("/promotions", json=body, headers=org.h("operator")).status_code == 403
    r = client.post("/promotions", json=body, headers=org.h("analyst"))
    assert r.status_code == 201, r.text
    planned = r.json()
    assert planned["status"] == "planned" and planned["product_count"] == 2
    # The earlier sale (days 40-46) is not recorded here, so it counts in the baseline: 30 / 12.5 = 2.4.
    assert planned["uplift_source"] == "estimated" and planned["uplift"] == 2.4
    assert planned["measurement"] is None and planned["estimate"]["uplift"] == 2.4

    rec = inventory_service.recommendation(db, org.org_id, b.id, fast=True)
    assert any("Promotion uplift applied to 7 forecast day(s): Summer ×2.4" in x for x in rec["assumptions"])
    assert [x["kind"] for x in rec["overrides_applied"]] == ["promotion"]

    override_service.create(
        db,
        org.org_id,
        org.users["analyst"],
        product_id=b.id,
        start_date=start,
        end_date=start,
        kind="set",
        value=1,
        reason="Shelf reset",
    )
    db.commit()
    points = [{"date": (AS_OF + timedelta(days=k)).isoformat(), "yhat": 5.0} for k in range(1, 15)]
    adjusted, applied = override_service.adjust_points(db, org.org_id, b.id, points)
    assert [p["yhat"] for p in adjusted][:10] == [5, 5, 1, 12, 12, 12, 12, 12, 12, 5]
    assert {x["kind"]: x["days"] for x in applied} == {"set": 1, "promotion": 6}

    patched = client.patch(
        f"/promotions/{planned['id']}", json={"uplift": 1.5, "expected_version": 1}, headers=org.h("analyst")
    )
    assert patched.status_code == 200 and patched.json()["uplift_source"] == "planner"
    stale = client.patch(
        f"/promotions/{planned['id']}", json={"uplift": 2, "expected_version": 1}, headers=org.h("analyst")
    )
    assert stale.status_code == 409
    adjusted, _ = override_service.adjust_points(db, org.org_id, a.id, points)
    assert adjusted[3]["yhat"] == 7.5 and adjusted[3]["yhat_model"] == 5.0 and adjusted[3]["lower"] is None
    actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.action.like("promotion.%"))))
    assert actions == {"promotion.created", "promotion.updated"}


def test_rules_and_isolation(client, org, other_org, db):
    a, b, _ = _seed(db, org.org_id)
    h = org.h("analyst")
    base = {"name": "X", "product_ids": [str(a.id)]}
    bad = [
        {**base, "start_date": _day(10), "end_date": _day(5)},
        {**base, "start_date": _day(0), "end_date": _day(100)},
        {"name": "X", "start_date": _day(0), "end_date": _day(1)},
        {**base, "start_date": _day(0), "end_date": _day(1), "uplift": 11},
        {"name": "X", "start_date": _day(0), "end_date": _day(1), "category": "Nope"},
    ]
    for body in bad:
        assert client.post("/promotions", json=body, headers=h).status_code == 422, body
    foreign = {"name": "X", "start_date": _day(0), "end_date": _day(1), "product_ids": [str(a.id)]}
    assert client.post("/promotions", json=foreign, headers=other_org.h("admin")).status_code == 422

    done = _past(client, org, a, first=60, last=66, discount=None, name="Old")
    url = f"/promotions/{done['id']}"
    assert client.patch(url, json={"end_date": _day(70)}, headers=h).status_code == 409
    assert client.patch(url, json={"notes": "Ran with a flyer"}, headers=h).json()["notes"] == "Ran with a flyer"
    no_est = client.post(
        "/promotions",
        json={
            "name": "Y",
            "start_date": _day(130),
            "end_date": _day(131),
            "product_ids": [str(b.id)],
            "use_estimate": False,
        },
        headers=h,
    ).json()
    assert no_est["uplift"] is None and no_est["uplift_source"] == "none"
    assert promotion_service.active_for(db, org.org_id, b.id, START, START + timedelta(days=200)) == []

    assert client.get(url, headers=other_org.h()).status_code == 404
    assert client.get("/promotions", headers=other_org.h()).json() == []
    assert len(client.get("/promotions", headers=org.h("viewer")).json()) == 2
    assert client.delete(url, headers=other_org.h("admin")).status_code == 404
    assert client.delete(url, headers=h).status_code == 204
    assert client.get(url, headers=h).status_code == 404
