"""Launch forecasts for new products from analogue products (ADR-044).

A new product has no sales to forecast from. The planner picks up to five similar products (analogues) and a launch
date. Each analogue's daily sales from its own first sale form a launch curve; the curves are averaged, smoothed and
multiplied by the planner's scale (for example 0.5 for "half as popular"). Once the new product sells, its own sales
correct the scale: after OBSERVE_DAYS days the correction has full weight. After HANDOVER_DAYS days of its own
history the product is forecast like any other product and the plan is no longer used.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import Product, ProductLaunch
from app.services import series
from app.services.product_service import get_product, org_as_of

MAX_ANALOGUES = 5
CURVE_DAYS = 180  # how much of each analogue's early life is used
MIN_ANALOGUE_DAYS = 28  # an analogue needs at least this much history after its first sale
OBSERVE_DAYS = 28  # own sales get full weight in the scale correction after this many days
HANDOVER_DAYS = 56  # with this much own history, normal forecasting takes over
RATIO_BOUNDS = (0.2, 5.0)  # the correction from early sales is clipped to this range
MIN_SCALE, MAX_SCALE = 0.05, 20.0


def get_plan(db: Session, org_id: uuid.UUID, product_id: uuid.UUID) -> ProductLaunch | None:
    return db.scalar(
        select(ProductLaunch).where(ProductLaunch.org_id == org_id, ProductLaunch.product_id == product_id)
    )


def _analogue_curves(db: Session, org_id: uuid.UUID, ids: list[uuid.UUID]) -> tuple[list[np.ndarray], list[dict]]:
    """Each usable analogue's daily sales from its first sale, plus a note per analogue."""
    loaded = series.load_many(db, org_id, ids)
    names = dict(db.execute(select(Product.id, Product.sku).where(Product.id.in_(ids))).tuples().all())
    curves, notes = [], []
    for pid in ids:
        y = loaded.get(pid)
        sku = names.get(pid, str(pid))
        if y is None or y.empty or not (y > 0).any():
            notes.append({"product_id": str(pid), "sku": sku, "used": False, "reason": "no sales"})
            continue
        first = int(np.argmax(y.values > 0))
        values = y.values[first : first + CURVE_DAYS].astype(float)
        if len(values) < MIN_ANALOGUE_DAYS:
            notes.append(
                {
                    "product_id": str(pid),
                    "sku": sku,
                    "used": False,
                    "reason": f"only {len(values)} days since its first sale (needs {MIN_ANALOGUE_DAYS})",
                }
            )
            continue
        curves.append(values)
        notes.append(
            {
                "product_id": str(pid),
                "sku": sku,
                "used": True,
                "days": len(values),
                "first_sale": y.index[first].date().isoformat(),
            }
        )
    return curves, notes


def _smooth(x: np.ndarray, window: int = 7) -> np.ndarray:
    if len(x) < window:
        return x
    pad = window // 2
    padded = np.pad(x, (pad, pad), mode="edge")
    return np.convolve(padded, np.ones(window) / window, mode="valid")


def build_curve(curves: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Mean launch curve and its spread across analogues, day by day from launch, smoothed over a week.

    Analogues with shorter histories stop contributing where their data ends; past the longest analogue the curve
    stays at its last smoothed level. With one analogue the spread is 30% of the level (a stated assumption)."""
    length = max(len(c) for c in curves)
    stack = np.full((len(curves), length), np.nan)
    for i, c in enumerate(curves):
        stack[i, : len(c)] = c
    mean = _smooth(np.nanmean(stack, axis=0))
    if len(curves) > 1:
        counts = np.sum(~np.isnan(stack), axis=0)
        sd = np.where(counts > 1, np.nanstd(stack, axis=0, ddof=0), 0.3 * np.nanmean(stack, axis=0))
        sd = _smooth(np.nan_to_num(sd))
    else:
        sd = 0.3 * mean
    return np.maximum(mean, 0.0), np.maximum(sd, 0.0)


def _at(arr: np.ndarray, k: int) -> float:
    if k < 0:
        return 0.0
    return float(arr[min(k, len(arr) - 1)])


def own_history(db: Session, org_id: uuid.UUID, product: Product, launch: date) -> np.ndarray:
    """The new product's daily sales since the launch date (empty before it sells)."""
    y, _ = series.load_series(db, org_id, product.id)
    if y.empty:
        return np.array([])
    y = y[y.index >= np.datetime64(launch)]
    return y.values.astype(float)


def applies(db: Session, org_id: uuid.UUID, product: Product) -> ProductLaunch | None:
    """The plan, when the product should still be forecast from it."""
    plan = get_plan(db, org_id, product.id)
    if plan is None:
        return None
    if len(own_history(db, org_id, product, plan.launch_date)) >= HANDOVER_DAYS:
        return None
    return plan


def forecast(db: Session, org_id: uuid.UUID, product: Product, plan: ProductLaunch, horizon: int) -> dict:
    """A forecast payload shaped like forecast_service.generate's, built from the analogues."""
    ids = [uuid.UUID(x) for x in plan.analogue_ids]
    curves, notes = _analogue_curves(db, org_id, ids)
    if not curves:
        raise ValidationFailed("None of the analogue products has enough sales history to build a launch curve")
    mean, sd = build_curve(curves)
    own = own_history(db, org_id, product, plan.launch_date)
    warnings = [
        "Launch forecast from analogue products: treat it as a planning estimate, not a measured forecast.",
    ]
    ratio, weight = 1.0, 0.0
    if len(own):
        expected = sum(_at(mean, k) * plan.scale for k in range(len(own)))
        if expected > 0:
            ratio = float(np.clip(own.sum() / expected, *RATIO_BOUNDS))
            weight = min(len(own) / OBSERVE_DAYS, 1.0)
        start = plan.launch_date + timedelta(days=len(own))
    else:
        as_of = org_as_of(db, org_id) or date.today()
        start = max(plan.launch_date, as_of + timedelta(days=1))
    effective = plan.scale * ratio**weight
    points = []
    for i in range(horizon):
        d = start + timedelta(days=i)
        k = (d - plan.launch_date).days
        yhat = _at(mean, k) * effective
        spread = _at(sd, k) * effective
        points.append(
            {
                "date": d.isoformat(),
                "yhat": round(yhat, 4),
                "lower": round(max(yhat - 1.28 * spread, 0.0), 4),
                "upper": round(yhat + 1.28 * spread, 4),
            }
        )
    upcoming = [p["yhat"] for p in points if p["yhat"] > 0]
    error_sd = float(np.mean([_at(sd, (start - plan.launch_date).days + i) for i in range(horizon)])) * effective
    if len(own):
        warnings.append(
            f"{len(own)} day(s) of own sales are {ratio:.2f}× the plan; the scale is corrected by "
            f"{weight:.0%} of that so far."
        )
    return {
        "product_id": str(product.id),
        "sku": product.sku,
        "as_of": (start - timedelta(days=1)).isoformat(),
        "horizon": horizon,
        "method": "launch_analogue",
        "method_reason": f"launch plan from {len(curves)} analogue product(s), scale {plan.scale:g}",
        "transformer_status": "not used for launches",
        "model_version": None,
        "points": points,
        "interval": {"method": "analogue_spread", "coverage": 0.8, "calibrated": False},
        "epistemic_uncertainty": None,
        "error_sd": round(error_sd, 4),
        "error_sd_source": "spread between analogue products' launches",
        "backtest": None,
        "signals": [],
        "warnings": warnings if upcoming else [*warnings, "The forecast is zero over this horizon."],
        "launch": {
            "launch_date": plan.launch_date.isoformat(),
            "scale": plan.scale,
            "effective_scale": round(effective, 4),
            "own_days": len(own),
            "analogues": notes,
        },
    }


def plan_out(plan: ProductLaunch) -> dict:
    return {
        "id": str(plan.id),
        "product_id": str(plan.product_id),
        "launch_date": plan.launch_date.isoformat(),
        "analogue_ids": list(plan.analogue_ids),
        "scale": plan.scale,
        "notes": plan.notes,
        "version": plan.version,
        "created_at": plan.created_at.isoformat(),
    }


def preview(db: Session, org_id: uuid.UUID, product_id: uuid.UUID, days: int = 91) -> dict:
    """The plan with its expected demand for the first `days` days after launch and running totals."""
    product = get_product(db, org_id, product_id)
    plan = get_plan(db, org_id, product_id)
    if plan is None:
        raise NotFound("This product has no launch plan")
    fc = forecast(db, org_id, product, plan, days)
    totals = {}
    for label, n in (("first_4_weeks", 28), ("first_13_weeks", 91)):
        end = plan.launch_date + timedelta(days=n)
        totals[label] = round(sum(p["yhat"] for p in fc["points"] if p["date"] < end.isoformat()), 1)
    own_days = fc["launch"]["own_days"]
    return {
        "plan": plan_out(plan),
        "active": own_days < HANDOVER_DAYS,
        "handover_days": HANDOVER_DAYS,
        "forecast": fc,
        "totals": totals,
    }


def save(
    db: Session,
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    launch_date: date,
    analogue_ids: list[uuid.UUID],
    scale: float,
    notes: str = "",
    expected_version: int | None = None,
) -> ProductLaunch:
    product = get_product(db, org_id, product_id)
    ids = list(dict.fromkeys(analogue_ids))
    if not 1 <= len(ids) <= MAX_ANALOGUES:
        raise ValidationFailed(f"Choose between 1 and {MAX_ANALOGUES} analogue products")
    if product.id in ids:
        raise ValidationFailed("A product cannot be its own analogue")
    found = set(db.scalars(select(Product.id).where(Product.org_id == org_id, Product.id.in_(ids))))
    if found != set(ids):
        raise ValidationFailed("An analogue product was not found")
    if not MIN_SCALE <= scale <= MAX_SCALE:
        raise ValidationFailed(f"Scale must be between {MIN_SCALE} and {MAX_SCALE}")
    curves, _ = _analogue_curves(db, org_id, ids)
    if not curves:
        raise ValidationFailed(
            f"None of the chosen products has at least {MIN_ANALOGUE_DAYS} days of sales since its first sale"
        )
    plan = db.scalar(
        select(ProductLaunch)
        .where(ProductLaunch.org_id == org_id, ProductLaunch.product_id == product.id)
        .with_for_update()
    )
    if plan is None:
        if expected_version not in (None, 0):
            raise Conflict("This launch plan was deleted; reload the page")
        plan = ProductLaunch(org_id=org_id, product_id=product.id, created_by=user_id, version=1)
        db.add(plan)
    else:
        if expected_version is not None and expected_version != plan.version:
            raise Conflict("Someone else changed this launch plan; reload the page")
        plan.version += 1
    plan.launch_date = launch_date
    plan.analogue_ids = [str(i) for i in ids]
    plan.scale = float(scale)
    plan.notes = notes.strip()[:500]
    db.flush()
    return plan


def delete(db: Session, org_id: uuid.UUID, product_id: uuid.UUID) -> None:
    plan = get_plan(db, org_id, product_id)
    if plan is None:
        raise NotFound("This product has no launch plan")
    db.delete(plan)


def suggest_analogues(db: Session, org_id: uuid.UUID, product_id: uuid.UUID, limit: int = 5) -> list[dict]:
    """Products in the same category (or any, if it has none) with enough history, newest launches first."""
    product = get_product(db, org_id, product_id)
    q = select(Product.id, Product.sku, Product.name, Product.category).where(
        Product.org_id == org_id, Product.id != product.id
    )
    if product.category:
        q = q.where(Product.category == product.category)
    rows = db.execute(q).all()
    if not rows:
        return []
    _, notes = _analogue_curves(db, org_id, [r.id for r in rows])
    usable = {n["product_id"]: n for n in notes if n["used"]}
    out = [
        {
            "product_id": str(r.id),
            "sku": r.sku,
            "name": r.name,
            "category": r.category,
            "first_sale": usable[str(r.id)]["first_sale"],
        }
        for r in rows
        if str(r.id) in usable
    ]
    out.sort(key=lambda x: x["first_sale"], reverse=True)
    return out[:limit]
