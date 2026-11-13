"""Quantity price breaks: a lower unit cost from a minimum order quantity (ADR-038). Pure functions."""

from __future__ import annotations

MAX_BREAKS = 10
MAX_EXTRA_RATIO = 0.5  # only suggest reaching the next break when it means at most 50% more units


class PriceBreakError(ValueError):
    pass


def normalise(breaks: list[dict], base_cost: float | None) -> list[dict]:
    """Validate and sort breaks: whole minimum quantities above 1, each tier cheaper than the one before."""
    if len(breaks) > MAX_BREAKS:
        raise PriceBreakError(f"At most {MAX_BREAKS} price breaks")
    if breaks and base_cost is None:
        raise PriceBreakError("Set the product's unit cost before adding price breaks")
    out = sorted(
        ({"min_qty": int(b["min_qty"]), "unit_cost": float(b["unit_cost"])} for b in breaks), key=lambda b: b["min_qty"]
    )
    prev_qty, prev_cost = 1, base_cost
    for b in out:
        if b["min_qty"] <= prev_qty:
            raise PriceBreakError("Each break needs a larger minimum quantity than the one before (and above 1)")
        if b["unit_cost"] < 0:
            raise PriceBreakError("Unit costs cannot be negative")
        if prev_cost is not None and b["unit_cost"] >= prev_cost:
            raise PriceBreakError(f"The price from {b['min_qty']} units must be lower than the price below it")
        prev_qty, prev_cost = b["min_qty"], b["unit_cost"]
    return out


def cost_at(base_cost: float | None, breaks: list[dict] | None, qty: int) -> float | None:
    """Unit cost for an order of ``qty`` units."""
    cost = base_cost
    for b in breaks or []:
        if qty >= b["min_qty"]:
            cost = b["unit_cost"]
    return cost


def next_break(base_cost: float | None, breaks: list[dict] | None, qty: int) -> dict | None:
    """The next cheaper tier above ``qty``, when reaching it means at most MAX_EXTRA_RATIO more units.

    Reported, never applied: ordering more than the suggestion has a holding cost the planner should weigh.
    """
    if qty <= 0 or base_cost is None:
        return None
    now = cost_at(base_cost, breaks, qty)
    for b in breaks or []:
        if b["min_qty"] > qty:
            extra = b["min_qty"] - qty
            if extra > qty * MAX_EXTRA_RATIO:
                return None
            assert now is not None
            return {
                "min_qty": b["min_qty"],
                "unit_cost": b["unit_cost"],
                "extra_units": extra,
                "order_value_now": round(qty * now, 2),
                "order_value_at_break": round(b["min_qty"] * b["unit_cost"], 2),
                "unit_saving": round(now - b["unit_cost"], 4),
            }
    return None
