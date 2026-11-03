"""Launch plans for new products from analogue products (ADR-044)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from sqlalchemy import select

from app.db.models import AuditEvent, Product, SalesRecord
from app.services import inventory_service, launch_service

START = date(2026, 1, 1)


def _product(db, org_id, sku, *, category="Tea", **kw):
    p = Product(org_id=org_id, sku=sku, name=sku, category=category, unit_cost=2.0, lead_time_days=7, **kw)
    db.add(p)
    db.flush()
    return p


def _sell(db, p, first: date, daily: list[float]):
    for i, q in enumerate(daily):
        db.add(SalesRecord(org_id=p.org_id, product_id=p.id, sale_date=first + timedelta(days=i), quantity=q))


def _ramp(days: int, peak: float) -> list[float]:
    """Sales that climb to `peak` over two weeks and then hold."""
    return [round(peak * min(1.0, (i + 1) / 14), 2) for i in range(days)]


def _seed(db, org_id):
    a = _product(db, org_id, "OLD-A")
    b = _product(db, org_id, "OLD-B")
    young = _product(db, org_id, "YOUNG")
    other = _product(db, org_id, "MUG", category="Mugs")
    new = _product(db, org_id, "NEW", on_hand=0, inventory_known=True)
    _sell(db, a, START, _ramp(120, 10))
    _sell(db, b, START + timedelta(days=30), _ramp(90, 20))  # a later launch with its own timeline
    _sell(db, young, START + timedelta(days=110), _ramp(10, 5))  # too little history to be an analogue
    _sell(db, other, START, _ramp(120, 3))
    db.commit()
    return a, b, young, new


def test_curve_aligns_analogues_from_their_first_sale():
    mean, sd = launch_service.build_curve([np.array(_ramp(60, 10)), np.array(_ramp(40, 20))])
    assert len(mean) == 60
    assert mean[30] == pytest.approx(15, abs=0.5)  # both analogues at their plateau
    assert mean[50] == pytest.approx(10, abs=0.5)  # only the longer one remains
    assert sd[30] == pytest.approx(5, abs=0.5)
    single_mean, single_sd = launch_service.build_curve([np.array(_ramp(60, 10))])
    assert single_sd[40] == pytest.approx(0.3 * single_mean[40])


def test_plan_drives_the_forecast_and_the_recommendation(client, org, other_org, db):
    a, b, young, new = _seed(db, org.org_id)
    launch = START + timedelta(days=130)  # after the organisation's latest sales date
    url = f"/products/{new.id}/launch"
    body = {"launch_date": launch.isoformat(), "analogue_ids": [str(a.id), str(b.id)], "scale": 0.5}
    assert client.put(url, json=body, headers=org.h("operator")).status_code == 403  # analysts plan demand
    r = client.put(url, json=body, headers=org.h("analyst"))
    assert r.status_code == 200, r.text
    out = r.json()
    fc = out["forecast"]
    assert out["active"] is True and fc["method"] == "launch_analogue"
    assert fc["points"][0]["date"] == launch.isoformat()
    assert fc["points"][0]["yhat"] < fc["points"][30]["yhat"]  # ramps up like the analogues
    assert fc["points"][30]["yhat"] == pytest.approx(0.5 * 15, rel=0.1)
    assert out["totals"]["first_4_weeks"] < out["totals"]["first_13_weeks"]
    assert [n["used"] for n in fc["launch"]["analogues"]] == [True, True]

    rec = inventory_service.recommendation(db, org.org_id, new.id, fast=True)
    assert rec["status"] == "ok" and rec["forecast_method"] == "launch_analogue" and rec["recommended_order_qty"] > 0

    listed = client.get("/launches", headers=org.h("viewer")).json()
    assert [x["sku"] for x in listed] == ["NEW"] and listed[0]["active"] is True
    assert client.get("/launches", headers=other_org.h()).json() == []
    assert client.get(url, headers=other_org.h()).status_code == 404
    assert client.put(url, json={**body, "expected_version": 0}, headers=org.h("analyst")).status_code == 409
    ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "launch.saved")).one()
    assert ev.details["scale"] == 0.5


def test_own_sales_correct_the_plan_then_take_over(client, org, db):
    a, b, _, new = _seed(db, org.org_id)
    launch = START + timedelta(days=100)
    launch_service.save(
        db, org.org_id, new.id, org.users["analyst"], launch_date=launch, analogue_ids=[a.id, b.id], scale=1.0
    )
    db.commit()
    # The new product sells twice what the analogues did over its first 28 days.
    mean, _ = launch_service.build_curve(launch_service._analogue_curves(db, org.org_id, [a.id, b.id])[0])
    _sell(db, new, launch, [round(2 * mean[k], 2) for k in range(28)])
    db.commit()
    fc = launch_service.forecast(db, org.org_id, new, launch_service.get_plan(db, org.org_id, new.id), 14)
    assert fc["launch"]["own_days"] == 28
    assert fc["launch"]["effective_scale"] == pytest.approx(2.0, rel=0.05)
    assert fc["points"][0]["date"] == (launch + timedelta(days=28)).isoformat()

    _sell(db, new, launch + timedelta(days=28), [20.0] * 28)  # 56 days of its own history now
    db.commit()
    assert launch_service.applies(db, org.org_id, new) is None
    rec = inventory_service.recommendation(db, org.org_id, new.id, fast=True)
    assert rec["forecast_method"] != "launch_analogue"
    assert client.get(f"/products/{new.id}/launch", headers=org.h()).json()["active"] is False


def test_validation_and_suggestions(client, org, db):
    a, _, young, new = _seed(db, org.org_id)
    url = f"/products/{new.id}/launch"
    h = org.h("analyst")
    day = START.isoformat()
    bad = client.put(url, json={"launch_date": day, "analogue_ids": [str(young.id)]}, headers=h)
    assert bad.status_code == 422 and "28 days" in bad.json()["error"]["message"]
    assert client.put(url, json={"launch_date": day, "analogue_ids": [str(new.id)]}, headers=h).status_code == 422
    too_many = [str(a.id)] * 6
    assert client.put(url, json={"launch_date": day, "analogue_ids": too_many}, headers=h).status_code == 422
    assert (
        client.put(url, json={"launch_date": day, "analogue_ids": [str(a.id)], "scale": 0}, headers=h).status_code
        == 422
    )

    suggested = client.get(f"{url}/analogues", headers=org.h("viewer")).json()
    assert [s["sku"] for s in suggested] == ["OLD-B", "OLD-A"]  # same category, enough history, newest first
    assert client.get(url, headers=h).json() == {"plan": None}
    assert client.put(url, json={"launch_date": day, "analogue_ids": [str(a.id)]}, headers=h).status_code == 200
    assert client.delete(url, headers=h).status_code == 204
    assert client.delete(url, headers=h).status_code == 404
