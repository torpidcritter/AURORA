"""Overstock and dead-stock report: where cash is tied up in stock that is not selling.

Everything here is computed from recorded sales and stock levels, relative to the organisation's latest sales date
(like the KPIs), not wall-clock today. Suggestions are descriptive (pause reordering, review on-order stock, consider
clearance); Aurora does not model price elasticity, so it does not propose discounts or predict their effect.
"""

from __future__ import annotations

import math
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Product, SalesRecord
from app.services.product_service import org_as_of

TRAILING_DAYS = 90


def report(
    db: Session,
    org_id: uuid.UUID,
    *,
    cover_days: int = 180,
    dead_days: int = 90,
    category: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Products whose stock covers more than ``cover_days`` of recent demand, or that have not sold in ``dead_days``.

    Days of cover = on hand / average daily sales over the trailing 90 days. Excess units are the stock above
    ``cover_days`` of that demand. Products with unknown inventory or nothing on hand are left out.
    """
    as_of = org_as_of(db, org_id)
    empty = {
        "as_of": None,
        "cover_days": cover_days,
        "dead_days": dead_days,
        "trailing_days": TRAILING_DAYS,
        "summary": {"products": 0, "dead": 0, "overstock": 0, "excess_value": 0.0, "stock_value": 0.0},
        "items": [],
        "total": 0,
        "limit": limit,
        "offset": offset,
    }
    if as_of is None:
        return empty
    since = as_of - timedelta(days=TRAILING_DAYS - 1)
    trailing = (
        select(SalesRecord.product_id.label("pid"), func.sum(SalesRecord.quantity).label("units"))
        .where(SalesRecord.org_id == org_id, SalesRecord.sale_date >= since, SalesRecord.sale_date <= as_of)
        .group_by(SalesRecord.product_id)
        .subquery()
    )
    last_sold = (
        select(SalesRecord.product_id.label("pid"), func.max(SalesRecord.sale_date).label("last"))
        .where(SalesRecord.org_id == org_id, SalesRecord.quantity > 0)
        .group_by(SalesRecord.product_id)
        .subquery()
    )
    q = (
        select(Product, trailing.c.units, last_sold.c.last)
        .outerjoin(trailing, trailing.c.pid == Product.id)
        .outerjoin(last_sold, last_sold.c.pid == Product.id)
        .where(Product.org_id == org_id, Product.inventory_known.is_(True), Product.on_hand > 0)
    )
    if category:
        q = q.where(Product.category == category)

    items = []
    for p, units, last in db.execute(q).all():
        units = float(units or 0.0)
        avg = units / TRAILING_DAYS
        since_sale = (as_of - last).days if last else None
        dead = since_sale is None or since_sale >= dead_days
        cover = round(p.on_hand / avg, 1) if avg > 0 else None
        if not dead and (cover is None or cover <= cover_days):
            continue
        keep = math.ceil(avg * cover_days)
        excess = max(p.on_hand - keep, 0)
        cost = p.unit_cost
        items.append(
            {
                "product_id": str(p.id),
                "sku": p.sku,
                "name": p.name,
                "category": p.category,
                "status": "dead" if dead else "overstock",
                "on_hand": p.on_hand,
                "on_order": p.on_order,
                "units_trailing": round(units, 3),
                "avg_daily_demand": round(avg, 3),
                "days_of_cover": cover,
                "last_sale_date": last.isoformat() if last else None,
                "days_since_last_sale": since_sale,
                "excess_units": excess,
                "unit_cost": cost,
                "stock_value": round(p.on_hand * cost, 2) if cost is not None else None,
                "excess_value": round(excess * cost, 2) if cost is not None else None,
                "suggestions": _suggest(p, dead, since_sale, cover, cover_days),
            }
        )

    items.sort(key=lambda r: (-(r["excess_value"] or 0.0), -r["excess_units"], r["sku"]))
    summary = {
        "products": len(items),
        "dead": sum(1 for r in items if r["status"] == "dead"),
        "overstock": sum(1 for r in items if r["status"] == "overstock"),
        "excess_value": round(sum(r["excess_value"] or 0.0 for r in items), 2),
        "stock_value": round(sum(r["stock_value"] or 0.0 for r in items), 2),
        "missing_cost": sum(1 for r in items if r["unit_cost"] is None),
    }
    return {
        **empty,
        "as_of": as_of.isoformat(),
        "summary": summary,
        "items": items[offset : offset + limit],
        "total": len(items),
    }


def _suggest(p: Product, dead: bool, since_sale: int | None, cover: float | None, cover_days: int) -> list[str]:
    out = []
    if dead:
        out.append(
            "No recorded sales yet."
            if since_sale is None
            else f"No sales in the last {since_sale} days. Review whether to clear, return or bundle this stock."
        )
    else:
        out.append(
            f"About {cover:,.0f} days of stock at the recent sales rate, against a target of {cover_days}. "
            "Pause reordering until it comes down."
        )
    if p.on_order:
        out.append(f"{p.on_order} more units are on order. Check whether that order can be reduced or delayed.")
    if p.unit_cost is None:
        out.append("No unit cost is recorded, so the value tied up is unknown.")
    return out
