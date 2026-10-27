"""Quantity price breaks and supplier reliability measured from purchase orders (ADR-038)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.db.models import Product, PurchaseOrder, PurchaseOrderLine, SalesRecord, Supplier
from app.domain import pricing
from app.services import inventory_service, supplier_performance

BREAKS = [{"min_qty": 100, "unit_cost": 1.8}, {"min_qty": 50, "unit_cost": 1.9}]


def test_price_break_rules():
    assert pricing.normalise(BREAKS, 2.0) == [{"min_qty": 50, "unit_cost": 1.9}, {"min_qty": 100, "unit_cost": 1.8}]
    for bad in (
        [{"min_qty": 50, "unit_cost": 2.0}],  # not cheaper than the base price
        [{"min_qty": 50, "unit_cost": 1.5}, {"min_qty": 100, "unit_cost": 1.6}],  # more for more
        [{"min_qty": 50, "unit_cost": 1.5}, {"min_qty": 50, "unit_cost": 1.4}],  # same quantity twice
        [{"min_qty": 1, "unit_cost": 1.5}],
    ):
        with pytest.raises(pricing.PriceBreakError):
            pricing.normalise(bad, 2.0)
    with pytest.raises(pricing.PriceBreakError):
        pricing.normalise(BREAKS, None)
    tiers = pricing.normalise(BREAKS, 2.0)
    assert [pricing.cost_at(2.0, tiers, q) for q in (1, 49, 50, 99, 100, 500)] == [2.0, 2.0, 1.9, 1.9, 1.8, 1.8]
    hint = pricing.next_break(2.0, tiers, 80)
    assert hint == {
        "min_qty": 100,
        "unit_cost": 1.8,
        "extra_units": 20,
        "order_value_now": 152.0,
        "order_value_at_break": 180.0,
        "unit_saving": 0.1,
    }
    assert pricing.next_break(2.0, tiers, 30) is None  # 50 is more than 50% above 30
    assert pricing.next_break(2.0, tiers, 120) is None  # already on the best tier


def _product(db, org_id, sku="A", **kw):
    p = Product(org_id=org_id, sku=sku, name=sku, unit_cost=2.0, **kw)
    db.add(p)
    db.flush()
    return p


def test_price_breaks_api_and_order_costs(client, org, db):
    p = _product(db, org.org_id)
    db.commit()
    url = f"/products/{p.id}/price-breaks"
    r = client.put(url, json={"expected_version": 1, "breaks": BREAKS}, headers=org.h("operator"))
    assert r.status_code == 200, r.text
    assert r.json()["price_breaks"][0] == {"min_qty": 50, "unit_cost": 1.9} and r.json()["version"] == 2
    assert client.put(url, json={"expected_version": 1, "breaks": []}, headers=org.h()).status_code == 409
    bad = client.put(url, json={"expected_version": 2, "breaks": [{"min_qty": 10, "unit_cost": 5}]}, headers=org.h())
    assert bad.status_code == 422 and "lower" in bad.json()["error"]["message"]
    assert client.get(f"/products/{p.id}", headers=org.h("viewer")).json()["price_breaks"][1]["min_qty"] == 100
    # A new base price below a break is refused until the breaks change.
    r = client.patch(f"/products/{p.id}/inventory", json={"expected_version": 2, "unit_cost": 1.85}, headers=org.h())
    assert r.status_code == 422 and "price breaks" in r.json()["error"]["message"]

    sup = client.post("/suppliers", json={"name": "Acme"}, headers=org.h()).json()
    lines = [{"product_id": str(p.id), "quantity": 60}]
    po = client.post("/purchase-orders", json={"supplier_id": sup["id"], "lines": lines}, headers=org.h()).json()
    assert po["lines"][0]["unit_cost"] == 1.9 and po["total_value"] == pytest.approx(114.0)
    lines = [{"product_id": str(p.id), "quantity": 10, "unit_cost": 2.5}]  # an explicit price is kept
    po = client.post("/purchase-orders", json={"lines": lines}, headers=org.h()).json()
    assert po["lines"][0]["unit_cost"] == 2.5
    # Clearing the breaks.
    r = client.put(url, json={"expected_version": 2, "breaks": []}, headers=org.h())
    assert r.status_code == 200 and r.json()["price_breaks"] == []
    assert client.get(f"/products/{p.id}", headers=org.h()).json()["price_breaks"] is None


def test_recommendation_prices_the_order_and_hints_at_the_next_break(client, org, db):
    p = _product(
        db, org.org_id, on_hand=0, inventory_known=True, lead_time_days=7, price_breaks=pricing.normalise(BREAKS, 2.0)
    )
    start = date(2026, 1, 1)
    for i in range(90):
        db.add(SalesRecord(org_id=org.org_id, product_id=p.id, sale_date=start + timedelta(days=i), quantity=4))
    db.commit()
    r = inventory_service.recommendation(db, org.org_id, p.id, fast=True)
    qty = r["recommended_order_qty"]
    assert qty > 0
    assert r["order_unit_cost"] == pricing.cost_at(2.0, p.price_breaks, qty)
    assert r["order_value"] == pytest.approx(qty * r["order_unit_cost"], abs=0.01)
    assert r["price_break"] == pricing.next_break(2.0, p.price_breaks, qty)


def _delivered(db, org_id, supplier_id, pid, *, sent_days_ago, lead_days, late=False, received=10, ordered=10):
    sent = datetime.now(UTC) - timedelta(days=sent_days_ago)
    got = sent + timedelta(days=lead_days)
    expected = (sent + timedelta(days=lead_days - (1 if late else 0))).date()
    n = (db.query(PurchaseOrder).filter(PurchaseOrder.org_id == org_id).count() or 0) + 1
    po = PurchaseOrder(
        org_id=org_id,
        number=n,
        status="received" if received == ordered else "cancelled",
        supplier_id=supplier_id,
        sent_at=sent,
        received_at=got if received == ordered else None,
        expected_date=expected,
    )
    db.add(po)
    db.flush()
    db.add(
        PurchaseOrderLine(
            org_id=org_id, purchase_order_id=po.id, product_id=pid, quantity=ordered, received_qty=received
        )
    )


def test_supplier_reliability_feeds_safety_stock(client, org, other_org, db):
    sup = Supplier(org_id=org.org_id, name="Slowboat", lead_time_days=7)
    db.add(sup)
    db.flush()
    p = _product(db, org.org_id, on_hand=20, inventory_known=True, supplier_id=sup.id)
    start = date(2026, 1, 1)
    for i in range(90):
        db.add(SalesRecord(org_id=org.org_id, product_id=p.id, sale_date=start + timedelta(days=i), quantity=5))
    db.commit()
    before = inventory_service.recommendation(db, org.org_id, p.id, fast=True)

    for i, (lead, late) in enumerate([(5, False), (9, True), (12, True), (6, False)]):
        _delivered(db, org.org_id, sup.id, p.id, sent_days_ago=100 - i * 20, lead_days=lead, late=late)
    _delivered(db, org.org_id, sup.id, p.id, sent_days_ago=10, lead_days=3, received=4, ordered=10)  # short, cancelled
    _delivered(db, org.org_id, sup.id, p.id, sent_days_ago=500, lead_days=40)  # outside the one-year window
    db.commit()

    stats = client.get("/suppliers/performance", headers=org.h("viewer")).json()
    assert len(stats) == 1
    s = stats[0]
    assert s["supplier_id"] == str(sup.id) and s["orders_sent"] == 5 and s["deliveries"] == 4
    assert s["avg_lead_days"] == 8.0 and s["stated_lead_days"] == 7
    assert s["lead_std_days"] == pytest.approx(3.16, abs=0.01)
    assert s["on_time_rate"] == 0.5
    assert s["fill_rate"] == pytest.approx(44 / 50)
    assert s["enough_data"] is True
    assert client.get("/suppliers/performance", headers=other_org.h()).json() == []

    after = inventory_service.recommendation(db, org.org_id, p.id, fast=True)
    assert after["safety_stock"] > before["safety_stock"]
    assert any("measured from 4 deliveries by Slowboat" in a for a in after["assumptions"])
    # An explicit lead-time spread in the request wins over the measured one.
    explicit = inventory_service.recommendation(db, org.org_id, p.id, overrides={"lead_time_std_days": 0.5}, fast=True)
    assert not any("measured from" in a for a in explicit["assumptions"])


def test_thin_evidence_is_not_used():
    assert supplier_performance.lead_std_for(None) is None
    assert supplier_performance.lead_std_for({"enough_data": False, "lead_std_days": 4.0}) is None
    assert supplier_performance.lead_std_for({"enough_data": True, "lead_std_days": 0.0}) is None
    assert supplier_performance.lead_std_for({"enough_data": True, "lead_std_days": 2.5}) == 2.5
    assert uuid  # keep the import for readers of the fixtures above
