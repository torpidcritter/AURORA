"""Budget-constrained ordering: the best mix of reorders when cash is limited (ADR-037).

For every product the inventory maths says to reorder, demand over the protection period (lead time + coverage) is
treated as normal with mean ``order_up_to - safety_stock`` and standard deviation ``(safety_stock / z) *
sqrt((L + C) / L)``, the same assumptions as the reorder point (ADR-006). Expected units short for a stock position S
is ``sd * G((S - mu) / sd)`` with the standard normal loss function G. Ordering more always reduces it, with
diminishing returns, so spending the budget one pack at a time on the step that avoids the most expected shortage per
unit of money is a good (greedy) allocation. Two policies are offered:

* ``service``: maximise expected units of demand served for the money (the greedy rule above).
* ``urgency``: fund products fully in risk order (critical first, then by urgency), skipping what no longer fits.

Minimum order quantities make the first step of a product larger; the greedy rule scores that step as a whole, which
can leave a product with a large MOQ unfunded even when part of the budget would have helped it. The response says
which products went unfunded and why.
"""

from __future__ import annotations

import heapq
import math
import uuid
from typing import Literal

from scipy.stats import norm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.db.models import Product
from app.services import inventory_service, settings_service

Policy = Literal["service", "urgency"]
MAX_STEPS = 60
RISK_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def expected_short(mu: float, sd: float, position: float) -> float:
    """Expected units of demand not met over the period, for demand ~ N(mu, sd) and stock position ``position``."""
    if sd <= 1e-9:
        return max(mu - position, 0.0)
    k = (position - mu) / sd
    return float(sd * (norm.pdf(k) - k * (1 - norm.cdf(k))))


def _steps(recommended: int, pack: int, moq: int) -> list[int]:
    """Order quantities to consider, as increments: the first reaches the MOQ, the rest add whole packs."""
    pack = max(pack, 1)
    first = max(pack, math.ceil(max(moq, 1) / pack) * pack)
    if recommended <= first:
        return [recommended]
    remaining = recommended - first
    # Large orders are considered in at most MAX_STEPS chunks, each a whole number of packs.
    chunk = max(pack, math.ceil(remaining / MAX_STEPS / pack) * pack)
    steps = [first]
    while remaining > 0:
        steps.append(min(chunk, remaining))
        remaining -= steps[-1]
    return steps


def _candidates(db: Session, org_id: uuid.UUID, product_ids: list[uuid.UUID] | None) -> tuple[list[dict], list[dict]]:
    eff = settings_service.for_org(db, org_id)
    recs = inventory_service.risk_matrix(db, org_id, product_ids=product_ids, org_settings=eff)
    products = {p.id: p for p in db.scalars(select(Product).where(Product.org_id == org_id))}
    candidates: list[dict] = []
    unfunded: list[dict] = []
    for r in recs:
        qty = int(r.get("recommended_order_qty") or 0)
        if r.get("status") != "ok" or qty <= 0:
            continue
        p = products[uuid.UUID(r["product_id"])]
        base = {"product_id": r["product_id"], "sku": r["sku"], "name": r["name"], "recommended_qty": qty}
        if p.unit_cost is None or p.unit_cost <= 0:
            unfunded.append({**base, "reason": "no unit cost, so it cannot be budgeted"})
            continue
        lead, cover = float(r["lead_time_days"]), float(r["coverage_days"])
        z = float(r["z"])
        mu = max(float(r["order_up_to"]) - float(r["safety_stock"]), 0.0)
        sd = (float(r["safety_stock"]) / z) * math.sqrt((lead + cover) / lead) if z > 0 else 0.0
        position = float(r["inventory_position"])
        short = expected_short(mu, sd, position)
        candidates.append(
            {
                **base,
                "risk_level": r["risk_level"],
                "urgency": float(r.get("urgency") or 0.0),
                "unit_cost": float(p.unit_cost),
                "mu": mu,
                "sd": sd,
                "position": position,
                "steps": _steps(qty, p.pack_size, p.min_order_qty),
                "taken": 0,
                "planned": 0,
                "short_before": short,
                "short_now": short,
            }
        )
    return candidates, unfunded


def _fund_by_service(candidates: list[dict], budget: float) -> float:
    remaining = budget
    heap: list[tuple[float, str, int]] = []

    def push(i: int) -> None:
        c = candidates[i]
        if c["taken"] >= len(c["steps"]):
            return
        step = c["steps"][c["taken"]]
        gain = c["short_now"] - expected_short(c["mu"], c["sd"], c["position"] + c["planned"] + step)
        heapq.heappush(heap, (-gain / (step * c["unit_cost"]), c["sku"], i))

    for i in range(len(candidates)):
        push(i)
    while heap:
        _, _, i = heapq.heappop(heap)
        c = candidates[i]
        step = c["steps"][c["taken"]]
        cost = step * c["unit_cost"]
        if cost > remaining + 1e-9:
            continue  # this product's next step no longer fits; cheaper steps of others may
        remaining -= cost
        c["planned"] += step
        c["taken"] += 1
        c["short_now"] = expected_short(c["mu"], c["sd"], c["position"] + c["planned"])
        push(i)
    return remaining


def _fund_by_urgency(candidates: list[dict], budget: float) -> float:
    remaining = budget
    for c in sorted(candidates, key=lambda c: (RISK_ORDER.get(c["risk_level"], 9), -c["urgency"], c["sku"])):
        cost = c["recommended_qty"] * c["unit_cost"]
        if cost <= remaining + 1e-9:
            c["planned"] = c["recommended_qty"]
            remaining -= cost
    return remaining


def plan(
    db: Session,
    org_id: uuid.UUID,
    *,
    budget: float,
    policy: Policy = "service",
    category: str | None = None,
    product_ids: list[uuid.UUID] | None = None,
) -> dict:
    if not math.isfinite(budget) or budget < 0:
        raise ValidationFailed("Budget must be zero or more")
    if category is not None:
        ids = list(db.scalars(select(Product.id).where(Product.org_id == org_id, Product.category == category)))
        wanted = set(product_ids) if product_ids is not None else None
        product_ids = [i for i in ids if wanted is None or i in wanted]
        if not product_ids:
            raise ValidationFailed(f"No products in {category}")
    candidates, unfunded = _candidates(db, org_id, product_ids)
    fund = _fund_by_service if policy == "service" else _fund_by_urgency
    remaining = fund(candidates, float(budget))

    lines = []
    short_before = short_after = short_full = full_cost = demand = 0.0
    for c in candidates:
        after = expected_short(c["mu"], c["sd"], c["position"] + c["planned"])
        short_before += c["short_before"]
        short_after += after
        short_full += expected_short(c["mu"], c["sd"], c["position"] + c["recommended_qty"])
        full_cost += c["recommended_qty"] * c["unit_cost"]
        demand += c["mu"]
        lines.append(
            {
                **{k: c[k] for k in ("product_id", "sku", "name", "risk_level", "recommended_qty", "unit_cost")},
                "planned_qty": c["planned"],
                "value": round(c["planned"] * c["unit_cost"], 2),
                "demand_in_period": round(c["mu"], 2),
                "expected_short_before": round(c["short_before"], 2),
                "expected_short_after": round(after, 2),
                "fill_rate_after": round(1 - after / c["mu"], 4) if c["mu"] > 0 else None,
            }
        )
        if c["planned"] == 0:
            if policy == "service":
                what = f"its smallest order ({c['steps'][0]} units, {c['steps'][0] * c['unit_cost']:,.2f})"
            else:
                what = f"its full order ({c['recommended_qty'] * c['unit_cost']:,.2f})"
            unfunded.append(
                {
                    **{k: c[k] for k in ("product_id", "sku", "name", "recommended_qty")},
                    "reason": f"{what} did not fit the remaining budget",
                }
            )
    lines.sort(key=lambda r: (-r["value"], r["sku"]))

    def fill(short: float) -> float | None:
        return round(1 - short / demand, 4) if demand > 0 else None

    return {
        "budget": round(budget, 2),
        "policy": policy,
        "spent": round(budget - remaining, 2),
        "remaining": round(remaining, 2),
        "full_cost": round(full_cost, 2),
        "products_considered": len(candidates),
        "products_funded": sum(1 for line in lines if line["planned_qty"] > 0),
        "expected_short": {
            "no_orders": round(short_before, 2),
            "this_plan": round(short_after, 2),
            "all_recommended": round(short_full, 2),
        },
        "fill_rate": {
            "no_orders": fill(short_before),
            "this_plan": fill(short_after),
            "all_recommended": fill(short_full),
        },
        "lines": lines,
        "unfunded": unfunded,
        "currency_note": "Costs use each product's unit cost; there is no currency conversion.",
    }
