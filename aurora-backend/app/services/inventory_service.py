"""Inventory recommendations, stockout risk, KPI rollup and prescriptive recommendations."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.db.models import Alert, Dataset, Product, SalesRecord
from app.domain.inventory import InventoryInputError, InventoryInputs, compute
from app.services import forecast_service, model_registry, series
from app.services.forecast_service import ForecastRequest
from app.services.product_service import get_product, org_as_of


def resolve_params(product: Product, overrides: dict | None = None) -> tuple[dict, list[str]]:
    s = get_settings()
    o = overrides or {}
    assumptions = []
    lead = o.get("lead_time_days") or product.lead_time_days
    if lead is None:
        lead = s.default_lead_time_days
        assumptions.append(f"Supplier lead time not set; default of {lead} days assumed")
    sl = o.get("service_level") or product.service_level
    if sl is None:
        sl = s.default_service_level
        assumptions.append(f"Service level not set; default {sl:.0%} cycle service level assumed")
    return {
        "lead_time_days": float(lead),
        "lead_time_std_days": float(o.get("lead_time_std_days") or 0.0),
        "coverage_days": float(o.get("coverage_days") or s.default_coverage_days),
        "service_level": float(sl),
        "on_hand": float(o["on_hand"]) if o.get("on_hand") is not None else float(product.on_hand),
        "on_order": float(o["on_order"]) if o.get("on_order") is not None else float(product.on_order),
        "pack_size": product.pack_size,
        "min_order_qty": product.min_order_qty,
    }, assumptions


def forecast_for(db: Session, org_id: uuid.UUID, product: Product, horizon: int, fast: bool) -> dict | None:
    y, _ = series.load_series(db, org_id, product.id)
    if y.empty:
        return None
    cached = forecast_service.latest_forecast(db, org_id, product, y, horizon)
    if cached is not None:
        return cached
    return forecast_service.generate(
        db,
        org_id,
        ForecastRequest(product_id=product.id, horizon=horizon, method="auto", mc_samples=0, fast=fast, persist=True),
    )


def recommendation(
    db: Session,
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    overrides: dict | None = None,
    fast: bool = False,
    forecast_override: list[float] | None = None,
    sigma_multiplier: float = 1.0,
) -> dict:
    product = get_product(db, org_id, product_id)
    params, assumptions = resolve_params(product, overrides)
    base = {"product_id": str(product.id), "sku": product.sku, "name": product.name}
    if not product.inventory_known and (overrides or {}).get("on_hand") is None:
        return {
            **base,
            "status": "unknown_inventory",
            "risk_level": "unknown",
            "message": "On-hand inventory has not been provided for this product, so no recommendation is made.",
            "assumptions": assumptions,
        }
    horizon = int(min(max(params["lead_time_days"] + params["coverage_days"], 7), forecast_service.MAX_HORIZON))
    fc = forecast_for(db, org_id, product, horizon, fast)
    if fc is None:
        return {
            **base,
            "status": "no_forecast",
            "risk_level": "unknown",
            "message": "No sales history, so demand cannot be forecast.",
            "assumptions": assumptions,
        }
    daily = forecast_override if forecast_override is not None else [p["yhat"] for p in fc["points"]]
    sigma = float(fc.get("error_sd") or 0.0) * sigma_multiplier
    try:
        res = compute(InventoryInputs(forecast=daily, sigma_d=sigma, **params))
    except InventoryInputError as exc:
        raise ValidationFailed(str(exc)) from exc
    unit_cost = product.unit_cost
    out = {
        **base,
        "status": "ok",
        **res.as_dict(),
        "lead_time_days": params["lead_time_days"],
        "coverage_days": params["coverage_days"],
        "service_level": params["service_level"],
        "on_hand": params["on_hand"],
        "on_order": params["on_order"],
        "forecast_method": fc["method"],
        "forecast_as_of": fc["as_of"],
        "sigma_source": fc.get("error_sd_source"),
        "order_value": round(res.recommended_order_qty * unit_cost, 2) if unit_cost is not None else None,
        "overstock_value": round(res.overstock_units * unit_cost, 2) if unit_cost is not None else None,
        "assumptions": assumptions + res.notes,
    }
    out["narrative"] = narrative(out)
    return out


def narrative(r: dict) -> str:
    """Plain-language summary built only from computed values."""
    parts = [
        f"{r['sku']}: forecast demand averages {r['mean_daily_demand']:.1f} units/day over the "
        f"{r['lead_time_days']:g}-day lead time ({r['forecast_method']})."
    ]
    if r["days_until_stockout"] is None:
        parts.append("No stockout is expected because forecast demand is zero.")
    else:
        parts.append(f"{r['on_hand']:g} on hand lasts about {r['days_until_stockout']:.1f} days.")
    parts.append(
        f"Reorder point is {r['reorder_point']:.0f} (incl. {r['safety_stock']:.0f} safety stock for a "
        f"{r['service_level']:.0%} cycle service level)."
    )
    if r["recommended_order_qty"] > 0:
        parts.append(
            f"Order {r['recommended_order_qty']} units now to cover lead time plus {r['coverage_days']:g} days."
        )
    elif r["overstock_units"] > 0:
        parts.append(f"Stock exceeds the order-up-to level by {r['overstock_units']:.0f} units.")
    else:
        parts.append("No order is needed yet.")
    return " ".join(parts)


def risk_matrix(
    db: Session, org_id: uuid.UUID, limit: int = 500, product_ids: list[uuid.UUID] | None = None
) -> list[dict]:
    q = select(Product.id).where(Product.org_id == org_id).order_by(Product.sku).limit(limit)
    if product_ids:
        q = q.where(Product.id.in_(product_ids))
    out = []
    for pid in db.scalars(q).all():
        r = recommendation(db, org_id, pid, fast=True)
        out.append(r)
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}
    out.sort(key=lambda r: (order.get(r["risk_level"], 5), -(r.get("urgency") or 0)))
    return out


def kpis(db: Session, org_id: uuid.UUID) -> dict:
    as_of = org_as_of(db, org_id)
    matrix = risk_matrix(db, org_id)
    known = [r for r in matrix if r["status"] == "ok"]
    levels = {
        lvl: sum(1 for r in matrix if r["risk_level"] == lvl)
        for lvl in ("critical", "high", "medium", "low", "unknown")
    }
    units_30 = prev_30 = 0.0
    if as_of:
        w0 = as_of - timedelta(days=29)
        p0 = w0 - timedelta(days=30)
        units_30 = float(
            db.scalar(
                select(func.coalesce(func.sum(SalesRecord.quantity), 0)).where(
                    SalesRecord.org_id == org_id, SalesRecord.sale_date >= w0, SalesRecord.sale_date <= as_of
                )
            )
            or 0
        )
        prev_30 = float(
            db.scalar(
                select(func.coalesce(func.sum(SalesRecord.quantity), 0)).where(
                    SalesRecord.org_id == org_id, SalesRecord.sale_date >= p0, SalesRecord.sale_date < w0
                )
            )
            or 0
        )
    open_alerts = (
        db.scalar(select(func.count()).select_from(Alert).where(Alert.org_id == org_id, Alert.status == "open")) or 0
    )
    crit_alerts = (
        db.scalar(
            select(func.count())
            .select_from(Alert)
            .where(Alert.org_id == org_id, Alert.status == "open", Alert.severity == "critical")
        )
        or 0
    )
    last_upload = db.scalar(
        select(func.max(Dataset.created_at)).where(Dataset.org_id == org_id, Dataset.status == "imported")
    )
    active = model_registry.get_active(db, org_id)
    health = round(100 - sum(r["urgency"] for r in known) / len(known), 1) if known else None
    return {
        "as_of": as_of.isoformat() if as_of else None,
        "products_total": len(matrix),
        "products_with_inventory": len(known),
        "risk_counts": levels,
        "inventory_health_score": health,
        "units_last_30d": round(units_30, 2),
        "units_prev_30d": round(prev_30, 2),
        "demand_change_pct": round(100 * (units_30 - prev_30) / prev_30, 2) if prev_30 > 0 else None,
        "forecast_units_next_7d": round(sum(r["mean_daily_demand"] * 7 for r in known), 1),
        "reorder_now": sum(1 for r in known if r["recommended_order_qty"] > 0),
        "reorder_value": round(sum(r["order_value"] or 0 for r in known), 2),
        "overstock_units": round(sum(r["overstock_units"] for r in known), 1),
        "overstock_value": round(sum(r["overstock_value"] or 0 for r in known), 2),
        "open_alerts": open_alerts,
        "critical_alerts": crit_alerts,
        "last_upload_at": last_upload.isoformat() if last_upload else None,
        "active_model": {"version": active.version, "wape": active.metrics.get("transformer_wape")} if active else None,
        "definitions": {
            "inventory_health_score": "100 minus the mean urgency score (0-100) of products with known inventory",
            "units_last_30d": "Units sold in the 30 days ending at as_of (the latest sales date)",
            "forecast_units_next_7d": "Sum over products with known inventory of 7 x mean forecast daily demand",
            "reorder_now": "Products whose inventory position is at or below the reorder point",
            "overstock_units": "Units above the order-up-to level (lead time + coverage demand + safety stock)",
        },
    }


def recommendations(db: Session, org_id: uuid.UUID, limit: int = 20) -> list[dict]:
    """Prescriptive action items, highest urgency first. Narratives use only computed values."""
    items = []
    for r in risk_matrix(db, org_id):
        if r["status"] == "unknown_inventory":
            items.append(
                {
                    "type": "data",
                    "priority": 4,
                    "sku": r["sku"],
                    "product_id": r["product_id"],
                    "title": f"Provide on-hand inventory for {r['sku']}",
                    "detail": r["message"],
                }
            )
            continue
        if r["status"] != "ok":
            continue
        if r["recommended_order_qty"] > 0:
            items.append(
                {
                    "type": "reorder",
                    "priority": {"critical": 0, "high": 1}.get(r["risk_level"], 2),
                    "sku": r["sku"],
                    "product_id": r["product_id"],
                    "risk_level": r["risk_level"],
                    "title": f"Reorder {r['recommended_order_qty']} units of {r['sku']}",
                    "detail": r["narrative"],
                    "quantity": r["recommended_order_qty"],
                    "value": r["order_value"],
                }
            )
        elif r["overstock_units"] > max(r["mean_daily_demand"] * 30, 1):
            items.append(
                {
                    "type": "overstock",
                    "priority": 3,
                    "sku": r["sku"],
                    "product_id": r["product_id"],
                    "title": f"{r['sku']} is overstocked by {r['overstock_units']:.0f} units",
                    "detail": r["narrative"],
                    "value": r["overstock_value"],
                }
            )
    items.sort(key=lambda i: i["priority"])
    return items[:limit]


def as_of_or_today(db: Session, org_id: uuid.UUID) -> date:
    return org_as_of(db, org_id) or date.today()
