"""Budget-constrained ordering (ADR-037)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from app.db.models import Product, SalesRecord
from app.services import budget_service


def test_expected_shortage_matches_simulation():
    rng = np.random.default_rng(0)
    demand = rng.normal(100, 20, 400_000)
    for position in (60, 100, 140):
        simulated = np.maximum(demand - position, 0).mean()
        assert budget_service.expected_short(100, 20, position) == pytest.approx(simulated, rel=0.02)
    assert budget_service.expected_short(100, 0, 70) == 30
    assert budget_service.expected_short(100, 0, 150) == 0
    values = [budget_service.expected_short(100, 20, s) for s in range(0, 200, 10)]
    assert all(a >= b for a, b in zip(values, values[1:], strict=False))  # more stock never means more shortage


def test_order_steps_respect_pack_and_moq():
    assert budget_service._steps(10, 1, 0) == [1] * 10
    assert budget_service._steps(48, 12, 30) == [36, 12]  # MOQ 30 rounds up to 3 packs
    assert budget_service._steps(20, 5, 50) == [20]  # the suggestion is already the whole order
    big = budget_service._steps(100_000, 6, 0)
    assert sum(big) == 100_000 and len(big) <= budget_service.MAX_STEPS + 1
    assert all(s % 6 == 0 for s in big[:-1])


def _product(db, org_id, sku, *, per_day, cost, on_hand=0, category="Tea", **kw):
    p = Product(
        org_id=org_id,
        sku=sku,
        name=sku,
        category=category,
        on_hand=on_hand,
        inventory_known=True,
        unit_cost=cost,
        lead_time_days=7,
        **kw,
    )
    db.add(p)
    db.flush()
    rng = np.random.default_rng(len(sku))
    start = date(2026, 1, 1)
    for i in range(120):
        q = max(0.0, round(per_day + rng.normal(0, per_day * 0.2)))
        db.add(SalesRecord(org_id=org_id, product_id=p.id, sale_date=start + timedelta(days=i), quantity=q))
    return p


def _seed(db, org_id):
    _product(db, org_id, "CHEAP-FAST", per_day=20, cost=1.0)
    _product(db, org_id, "PRICEY-SLOW", per_day=2, cost=40.0)
    _product(db, org_id, "MID", per_day=8, cost=5.0, pack_size=10)
    _product(db, org_id, "NO-COST", per_day=5, cost=None)
    _product(db, org_id, "STOCKED", per_day=1, cost=1.0, on_hand=10_000)
    _product(db, org_id, "MUG", per_day=3, cost=2.0, category="Mugs")
    db.commit()


def _plan(client, org, **body):
    r = client.post("/purchase-orders/budget-plan", json=body, headers=org.h("viewer"))
    assert r.status_code == 200, r.text
    return r.json()


def test_plans_within_the_budget(client, org, db):
    _seed(db, org.org_id)
    full = _plan(client, org, budget=1e9)
    assert full["products_considered"] == 4  # STOCKED needs nothing; NO-COST cannot be budgeted
    assert [u["sku"] for u in full["unfunded"]] == ["NO-COST"] and "unit cost" in full["unfunded"][0]["reason"]
    assert full["spent"] == pytest.approx(full["full_cost"])
    assert all(line["planned_qty"] == line["recommended_qty"] for line in full["lines"])
    es = full["expected_short"]
    assert es["this_plan"] == pytest.approx(es["all_recommended"]) and es["no_orders"] > es["this_plan"]

    none = _plan(client, org, budget=0)
    assert none["spent"] == 0 and none["products_funded"] == 0
    assert none["expected_short"]["this_plan"] == pytest.approx(none["expected_short"]["no_orders"])

    budget = round(full["full_cost"] * 0.4, 2)
    part = _plan(client, org, budget=budget)
    assert 0 < part["spent"] <= budget and part["remaining"] == pytest.approx(budget - part["spent"], abs=0.01)
    assert sum(line["value"] for line in part["lines"]) == pytest.approx(part["spent"], abs=0.05)
    es = part["expected_short"]
    assert es["all_recommended"] <= es["this_plan"] < es["no_orders"]
    for line in part["lines"]:
        assert line["planned_qty"] <= line["recommended_qty"]
        if line["sku"] == "MID" and line["planned_qty"]:
            assert line["planned_qty"] % 10 == 0  # whole packs
    # Spending on what avoids the most shortage per unit of money beats funding by risk order alone.
    by_risk = _plan(client, org, budget=budget, policy="urgency")
    assert by_risk["spent"] <= budget
    assert part["expected_short"]["this_plan"] <= by_risk["expected_short"]["this_plan"] + 1e-6


def test_category_filter_and_isolation(client, org, other_org, db):
    _seed(db, org.org_id)
    mugs = _plan(client, org, budget=1e6, category="Mugs")
    assert [line["sku"] for line in mugs["lines"]] == ["MUG"]
    r = client.post("/purchase-orders/budget-plan", json={"budget": 10, "category": "Mugs"}, headers=other_org.h())
    assert r.status_code == 422
    assert client.post("/purchase-orders/budget-plan", json={"budget": -1}, headers=org.h()).status_code == 422


def test_drafts_from_a_plan_use_its_quantities(client, org, db):
    _seed(db, org.org_id)
    plan = _plan(client, org, budget=200)
    chosen = {line["product_id"]: line["planned_qty"] for line in plan["lines"] if line["planned_qty"] > 0}
    assert chosen
    r = client.post("/purchase-orders/suggest", json={"quantities": chosen}, headers=org.h("operator"))
    assert r.status_code == 201, r.text
    lines = [ln for po in r.json()["created"] for ln in po["lines"]]
    assert {ln["product_id"]: ln["quantity"] for ln in lines} == chosen
    assert all(po["notes"] == "Drafted from a budget plan." for po in r.json()["created"])
    assert all(ln["suggested_qty"] >= ln["quantity"] for ln in lines)
    zero = {plan["lines"][0]["product_id"]: 0}
    assert client.post("/purchase-orders/suggest", json={"quantities": zero}, headers=org.h()).status_code == 422
