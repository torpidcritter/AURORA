from __future__ import annotations

import math

import pytest

from app.domain.inventory import (
    InventoryInputError,
    InventoryInputs,
    classify,
    compute,
    days_until_stockout,
    stockout_probability,
    z_for,
)


def inp(**kw):
    base = dict(
        forecast=[10.0] * 14,
        sigma_d=3.0,
        on_hand=100,
        on_order=0,
        lead_time_days=7,
        coverage_days=14,
        service_level=0.95,
    )
    base.update(kw)
    return InventoryInputs(**base)


def test_textbook_safety_stock_and_rop():
    r = compute(inp())
    z = 1.6448536269514722
    assert r.z == pytest.approx(z)
    assert r.safety_stock == pytest.approx(z * 3 * math.sqrt(7))  # sqrt(L), not L
    assert r.reorder_point == pytest.approx(70 + z * 3 * math.sqrt(7))
    assert r.order_up_to == pytest.approx(10 * 21 + r.safety_stock)


def test_safety_stock_scales_with_sqrt_lead_time():
    a = compute(inp(lead_time_days=4)).safety_stock
    b = compute(inp(lead_time_days=16)).safety_stock
    assert b / a == pytest.approx(2.0)


def test_lead_time_variability_increases_safety_stock():
    r = compute(inp(lead_time_std_days=2))
    assert r.safety_stock == pytest.approx(z_for(0.95) * math.sqrt(7 * 9 + 100 * 4))


def test_order_quantity_rules():
    r = compute(inp(on_hand=50))  # below ROP ~83
    assert r.should_reorder
    expected = math.ceil(r.order_up_to - 50)
    assert r.recommended_order_qty == expected
    r = compute(inp(on_hand=50, pack_size=12))
    assert r.recommended_order_qty % 12 == 0 and r.recommended_order_qty >= r.qty_to_order_up_to
    r = compute(inp(on_hand=50, min_order_qty=500))
    assert r.recommended_order_qty == 500
    r = compute(inp(on_hand=500))  # above ROP: no order
    assert not r.should_reorder and r.recommended_order_qty == 0
    r = compute(inp(on_hand=50, on_order=100))  # on-order counts toward inventory position
    assert not r.should_reorder


def test_days_until_stockout():
    assert days_until_stockout([10, 10, 10], 25) == pytest.approx(2.5)
    assert days_until_stockout([10, 10, 10], 0) == 0.0
    assert days_until_stockout([10, 10], 50) == pytest.approx(5.0)  # extrapolates with horizon mean
    assert days_until_stockout([0, 0, 0], 5) is None
    assert days_until_stockout([0, 5, 5], 5) == pytest.approx(2.0)


def test_stockout_probability():
    assert stockout_probability([10] * 7, 3, 70, 7) == pytest.approx(0.5)
    assert stockout_probability([10] * 7, 0, 69, 7) == 1.0
    assert stockout_probability([10] * 7, 0, 71, 7) == 0.0
    assert stockout_probability([10] * 7, 3, 1000, 7) < 1e-6


def test_zero_demand():
    r = compute(inp(forecast=[0.0] * 14, sigma_d=0.0, on_hand=40))
    assert r.days_until_stockout is None and r.recommended_order_qty == 0
    assert r.risk_level == "low" and r.overstock_units == 40
    assert r.days_of_cover is None


def test_urgency_and_levels():
    r = compute(inp(on_hand=0))
    assert r.urgency_components["time"] == 100 and r.risk_level == "critical"
    assert r.urgency == pytest.approx(0.4 * 100 + 0.35 * 100 + 0.25 * 100, abs=0.01)
    r = compute(inp(on_hand=1000))
    assert r.risk_level == "low" and r.urgency < 25
    assert classify(10, 1.5, 7) == "critical"
    assert classify(10, 5, 7) == "high"
    assert classify(30, None, 7) == "medium"
    assert classify(80, None, 7) == "critical"


@pytest.mark.parametrize(
    "kw",
    [
        {"forecast": []},
        {"forecast": [1, -1]},
        {"forecast": [float("nan")]},
        {"on_hand": -5},
        {"lead_time_days": 0},
        {"service_level": 1.0},
        {"service_level": 0.3},
        {"sigma_d": -1},
        {"pack_size": 0},
        {"coverage_days": 1000},
    ],
)
def test_invalid_inputs_rejected(kw):
    with pytest.raises(InventoryInputError):
        compute(inp(**kw))
