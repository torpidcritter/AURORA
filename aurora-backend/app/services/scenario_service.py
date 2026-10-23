"""What-if simulation: apply bounded adjustments to the baseline forecast and recompute inventory decisions."""

from __future__ import annotations

import math
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.db.models import ExternalSignal
from app.services import inventory_service
from app.services.product_service import get_product

SCENARIO_TYPES = {
    "demand_increase": {
        "description": "Percentage increase applied to every forecast day",
        "params": {"pct": {"min": 0, "max": 300, "default": 20}},
    },
    "demand_decrease": {
        "description": "Percentage decrease applied to every forecast day",
        "params": {"pct": {"min": 0, "max": 95, "default": 20}},
    },
    "promotion_uplift": {
        "description": "Multiplicative uplift over a window of forecast days",
        "params": {
            "pct": {"min": 0, "max": 500, "default": 50},
            "start_day": {"min": 1, "max": 90, "default": 1},
            "duration_days": {"min": 1, "max": 90, "default": 7},
        },
    },
    "festival_uplift": {
        "description": "Multiplicative uplift over a window of forecast days",
        "params": {
            "pct": {"min": 0, "max": 500, "default": 80},
            "start_day": {"min": 1, "max": 90, "default": 1},
            "duration_days": {"min": 1, "max": 90, "default": 5},
        },
    },
    "lead_time_increase": {
        "description": "Supplier disruption: lead time becomes `days` (+ optional variability)",
        "params": {"days": {"min": 1, "max": 180, "default": 15}, "std_days": {"min": 0, "max": 60, "default": 0}},
    },
    "uncertainty_stress": {
        "description": "Increase demand-error variance by `variance_pct` percent",
        "params": {"variance_pct": {"min": 0, "max": 400, "default": 50}},
    },
    "service_level_change": {
        "description": "Target a different cycle service level",
        "params": {"service_level": {"min": 0.51, "max": 0.999, "default": 0.98}},
    },
    "apply_signals": {
        "description": "Apply stored external signals with an explicit sensitivity: "
        "daily multiplier = 1 + magnitude x sensitivity_pct/100",
        "params": {"sensitivity_pct": {"min": 0, "max": 200, "default": 30}},
    },
}


def _param(adj: dict, name: str) -> float:
    spec = SCENARIO_TYPES[adj["type"]]["params"][name]
    v = adj.get("params", {}).get(name, spec["default"])
    try:
        v = float(v)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(f"{adj['type']}.{name} must be a number") from exc
    if not math.isfinite(v) or not spec["min"] <= v <= spec["max"]:
        raise ValidationFailed(f"{adj['type']}.{name} must be between {spec['min']} and {spec['max']}")
    return v


def simulate(db: Session, org_id: uuid.UUID, product_id: uuid.UUID, adjustments: list[dict], horizon: int = 28) -> dict:
    if not 1 <= len(adjustments) <= 5:
        raise ValidationFailed("Provide between 1 and 5 adjustments")
    for a in adjustments:
        if a.get("type") not in SCENARIO_TYPES:
            raise ValidationFailed(
                f"Unknown scenario type {a.get('type')!r}", details={"allowed": list(SCENARIO_TYPES)}
            )
        unknown = set(a.get("params", {})) - set(SCENARIO_TYPES[a["type"]]["params"])
        if unknown:
            raise ValidationFailed(f"Unknown parameters for {a['type']}: {sorted(unknown)}")
    product = get_product(db, org_id, product_id)
    baseline = inventory_service.recommendation(db, org_id, product_id)
    if baseline.get("status") != "ok":
        return {"product_id": str(product_id), "status": baseline.get("status"), "baseline": baseline, "scenario": None}

    overrides: dict = {}
    sigma_mult = 1.0
    applied = []
    # forecast long enough to cover the (possibly extended) lead time plus the coverage window
    lead = max(
        [_param(a, "days") for a in adjustments if a["type"] == "lead_time_increase"] + [baseline["lead_time_days"]]
    )
    horizon = int(min(max(horizon, lead + baseline["coverage_days"], 7), 90))
    fc = inventory_service.forecast_for(db, org_id, product, horizon, fast=False)
    assert fc is not None
    base_curve = [p["yhat"] for p in fc["points"]][:horizon]
    curve = list(base_curve)
    start_date = date.fromisoformat(fc["as_of"]) + timedelta(days=1)
    for a in adjustments:
        t = a["type"]
        if t == "demand_increase":
            k = 1 + _param(a, "pct") / 100
            curve = [v * k for v in curve]
        elif t == "demand_decrease":
            k = 1 - _param(a, "pct") / 100
            curve = [v * k for v in curve]
        elif t in ("promotion_uplift", "festival_uplift"):
            k = 1 + _param(a, "pct") / 100
            s0 = int(_param(a, "start_day")) - 1
            d = int(_param(a, "duration_days"))
            curve = [v * k if s0 <= i < s0 + d else v for i, v in enumerate(curve)]
        elif t == "lead_time_increase":
            overrides["lead_time_days"] = _param(a, "days")
            overrides["lead_time_std_days"] = _param(a, "std_days")
        elif t == "uncertainty_stress":
            sigma_mult *= math.sqrt(1 + _param(a, "variance_pct") / 100)
        elif t == "service_level_change":
            overrides["service_level"] = _param(a, "service_level")
        elif t == "apply_signals":
            sens = _param(a, "sensitivity_pct") / 100
            end_date = start_date + timedelta(days=len(curve) - 1)
            sigs = db.scalars(
                select(ExternalSignal).where(
                    ExternalSignal.org_id == org_id,
                    ExternalSignal.start_date <= end_date,
                    ExternalSignal.end_date >= start_date,
                    (ExternalSignal.product_id.is_(None)) | (ExternalSignal.product_id == product_id),
                )
            ).all()
            for i in range(len(curve)):
                d = start_date + timedelta(days=i)
                m = 1.0
                for s in sigs:
                    if s.start_date <= d <= s.end_date:
                        m *= max(1 + s.magnitude * sens, 0.0)
                curve[i] *= m
            a = {**a, "signals_applied": [s.name for s in sigs]}
        applied.append(a)
    scenario = inventory_service.recommendation(
        db, org_id, product_id, overrides=overrides, forecast_override=curve, sigma_multiplier=sigma_mult
    )
    delta_keys = (
        "safety_stock",
        "reorder_point",
        "recommended_order_qty",
        "days_until_stockout",
        "stockout_probability_lead_time",
        "urgency",
    )
    deltas = {}
    for k in delta_keys:
        b, s = baseline.get(k), scenario.get(k)
        deltas[k] = None if b is None or s is None else round(s - b, 4)
    dates = [(start_date + timedelta(days=i)).isoformat() for i in range(len(curve))]
    return {
        "product_id": str(product_id),
        "sku": product.sku,
        "status": "ok",
        "adjustments": applied,
        "curve": [
            {"date": d, "baseline": round(b, 4), "scenario": round(s, 4)}
            for d, b, s in zip(dates, base_curve, curve, strict=True)
        ],
        "baseline": baseline,
        "scenario": scenario,
        "deltas": deltas,
    }
