"""Supplier reliability measured from purchase orders (ADR-038): actual lead time and its spread, on-time delivery
and fill rate over the last year. Measured lead-time variability feeds safety stock once there is enough evidence."""

from __future__ import annotations

import math
import statistics
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import PurchaseOrder, PurchaseOrderLine, Supplier

WINDOW_DAYS = 365
MIN_DELIVERIES = 3  # below this the spread of lead times is not used for safety stock


def for_org(db: Session, org_id: uuid.UUID) -> dict[uuid.UUID, dict]:
    since = datetime.now(UTC) - timedelta(days=WINDOW_DAYS)
    stated = dict(
        db.execute(select(Supplier.id, Supplier.lead_time_days).where(Supplier.org_id == org_id)).tuples().all()
    )
    orders = db.execute(
        select(
            PurchaseOrder.supplier_id,
            PurchaseOrder.status,
            PurchaseOrder.sent_at,
            PurchaseOrder.received_at,
            PurchaseOrder.expected_date,
            func.coalesce(func.sum(PurchaseOrderLine.quantity), 0),
            func.coalesce(func.sum(PurchaseOrderLine.received_qty), 0),
        )
        .join(PurchaseOrderLine, PurchaseOrderLine.purchase_order_id == PurchaseOrder.id, isouter=True)
        .where(
            PurchaseOrder.org_id == org_id,
            PurchaseOrder.supplier_id.is_not(None),
            PurchaseOrder.sent_at.is_not(None),
            PurchaseOrder.sent_at >= since,
        )
        .group_by(PurchaseOrder.id)
    ).all()
    acc: dict[uuid.UUID, dict] = defaultdict(
        lambda: {"leads": [], "on_time": [], "ordered": 0, "received": 0, "sent": 0}
    )
    for sid, status, sent_at, received_at, expected, qty, rec in orders:
        a = acc[sid]
        a["sent"] += 1
        if status in ("received", "cancelled"):  # closed orders count towards the fill rate
            a["ordered"] += int(qty)
            a["received"] += int(rec)
        if status == "received" and received_at is not None:
            a["leads"].append((received_at - sent_at).total_seconds() / 86400)
            if expected is not None:
                a["on_time"].append(received_at.date() <= expected)
    out = {}
    for sid, a in acc.items():
        leads = a["leads"]
        out[sid] = {
            "orders_sent": a["sent"],
            "deliveries": len(leads),
            "stated_lead_days": stated.get(sid),
            "avg_lead_days": round(statistics.fmean(leads), 1) if leads else None,
            "lead_std_days": round(statistics.stdev(leads), 2) if len(leads) >= 2 else None,
            "on_time_rate": round(sum(a["on_time"]) / len(a["on_time"]), 4) if a["on_time"] else None,
            "fill_rate": round(a["received"] / a["ordered"], 4) if a["ordered"] else None,
            "enough_data": len(leads) >= MIN_DELIVERIES,
        }
    return out


def lead_std_for(stats: dict | None) -> float | None:
    """The measured lead-time standard deviation to use for safety stock, or None when the evidence is too thin."""
    if not stats or not stats["enough_data"] or not stats["lead_std_days"]:
        return None
    v = float(stats["lead_std_days"])
    return v if math.isfinite(v) and v > 0 else None
