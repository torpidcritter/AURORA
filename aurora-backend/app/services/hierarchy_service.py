"""Category and organisation-level forecasts that add up to the product forecasts (ADR-035).

Two ways to forecast a group of products are compared on the group's own history:

* bottom-up: the sum of each product's decision forecast (the one inventory uses, with planner overrides), and
* direct: a statistical model fitted to the group's total daily sales.

Both are backtested at the group level on the same rolling origins. The caller picks how the two are reconciled so
that product numbers always sum to the group number: ``bottom_up`` keeps product forecasts and sums them; ``top_down``
keeps the direct group forecast and splits it across products in proportion to their own forecasts.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Literal

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.db.models import Product, SalesRecord
from app.ml import backtesting, metrics
from app.ml.conformal import per_step_quantiles
from app.ml.statistical import forecast as stat_forecast
from app.services import forecast_service, inventory_service, override_service, series
from app.services.forecast_service import BACKTEST_FOLDS, MAX_HORIZON, STAT_METHODS
from app.services.product_service import org_as_of

MAX_PRODUCTS = 200
HISTORY_DAYS = 90
Reconcile = Literal["bottom_up", "top_down"]
UNCATEGORISED = "Uncategorised"


def categories(db: Session, org_id: uuid.UUID) -> list[dict]:
    """Categories with product counts and units sold in the 30 days up to the organisation's latest sale."""
    as_of = org_as_of(db, org_id)
    counts = db.execute(
        select(Product.category, func.count(Product.id)).where(Product.org_id == org_id).group_by(Product.category)
    ).all()
    units: dict[str | None, float] = {}
    if as_of is not None:
        units = {
            c: float(u)
            for c, u in db.execute(
                select(Product.category, func.sum(SalesRecord.quantity))
                .join(Product, Product.id == SalesRecord.product_id)
                .where(SalesRecord.org_id == org_id, SalesRecord.sale_date > as_of - timedelta(days=30))
                .group_by(Product.category)
            ).all()
        }
    out = [
        {"category": c, "label": c or UNCATEGORISED, "products": int(n), "units_30d": round(units.get(c, 0.0), 2)}
        for c, n in counts
    ]
    return sorted(out, key=lambda r: (-r["units_30d"], r["label"].lower()))


def _products(db: Session, org_id: uuid.UUID, level: str, category: str | None) -> list[Product]:
    q = select(Product).where(Product.org_id == org_id)
    if level == "category":
        q = q.where(Product.category.is_(None) if category is None else Product.category == category)
    return list(db.scalars(q.order_by(Product.sku)).all())


def _aligned(y: pd.Series, idx: pd.DatetimeIndex) -> np.ndarray:
    """A product's daily series on the group's date range; days before its first sale or after its last are 0."""
    return y.reindex(idx).fillna(0.0).to_numpy(dtype=float)


def _product_forecaster(method: str):
    """The statistical forecaster used to backtest a product's bottom-up contribution."""
    m = method if method in STAT_METHODS else "seasonal_naive"

    def fn(hist: np.ndarray, h: int) -> np.ndarray:
        nz = np.flatnonzero(hist)
        start = int(nz[0]) if nz.size else len(hist)
        own = hist[start:]  # ignore the zero padding before the product existed
        if len(own) < backtesting.MIN_TRAIN:
            return np.full(h, float(own.mean()) if len(own) else 0.0)
        return stat_forecast(m, own, h)

    return fn


def forecast(
    db: Session,
    org_id: uuid.UUID,
    *,
    level: Literal["total", "category"],
    category: str | None,
    horizon: int,
    coverage: float,
    reconcile: Reconcile | None,
) -> dict:
    if not 1 <= horizon <= MAX_HORIZON:
        raise ValidationFailed(f"horizon must be between 1 and {MAX_HORIZON}")
    if coverage not in forecast_service.SUPPORTED_COVERAGES:
        raise ValidationFailed(f"coverage must be one of {forecast_service.SUPPORTED_COVERAGES}")
    products = _products(db, org_id, level, category)
    label = "All products" if level == "total" else (category or UNCATEGORISED)
    if not products:
        raise ValidationFailed(f"No products in {label}")
    if len(products) > MAX_PRODUCTS:
        raise ValidationFailed(
            f"{label} has {len(products)} products; group forecasts handle up to {MAX_PRODUCTS}. "
            "Forecast a category instead."
        )
    as_of = org_as_of(db, org_id)
    if as_of is None:
        raise ValidationFailed("No sales history yet; upload data first")

    by_id = series.load_many(db, org_id, [p.id for p in products])
    with_history = [p for p in products if p.id in by_id and not by_id[p.id].empty]
    if not with_history:
        raise ValidationFailed(f"No product in {label} has sales history yet")
    start = min(by_id[p.id].index[0] for p in with_history)
    idx = pd.date_range(start, pd.Timestamp(as_of), freq="D")
    aligned = {p.id: _aligned(by_id[p.id], idx) for p in with_history}
    total = np.sum(np.vstack(list(aligned.values())), axis=0)
    dates = [as_of + timedelta(days=i + 1) for i in range(horizon)]
    warnings: list[str] = []
    excluded = [
        {"product_id": str(p.id), "sku": p.sku, "reason": "no sales history"} for p in products if p not in with_history
    ]

    # Bottom-up: each product's decision forecast (with planner overrides), placed on the group's dates.
    product_rows: list[dict] = []
    for p in with_history:
        last = by_id[p.id].index[-1].date()
        gap = (as_of - last).days
        if horizon + gap > MAX_HORIZON:
            excluded.append(
                {"product_id": str(p.id), "sku": p.sku, "reason": f"no sales for {gap} days; counted as zero"}
            )
            product_rows.append({"product": p, "method": None, "points": np.zeros(horizon), "overrides": 0})
            continue
        fc = inventory_service.forecast_for(db, org_id, p, horizon + gap, fast=True)
        if fc is None:  # pragma: no cover - history exists, so a forecast is always produced
            continue
        adjusted, applied = override_service.adjust_points(db, org_id, p.id, fc["points"])
        by_date = {pt["date"]: float(pt["yhat"]) for pt in adjusted}
        vals = np.array([by_date.get(d.isoformat(), 0.0) for d in dates])
        product_rows.append({"product": p, "method": fc.get("method"), "points": vals, "overrides": len(applied)})
    bottom_up = np.sum(np.vstack([r["points"] for r in product_rows]), axis=0)

    # Direct: the best statistical model on the group's total, with conformal intervals from its own errors.
    ev = forecast_service.baseline_evaluation(None, org_id, uuid.uuid4(), pd.Series(total, index=idx), horizon)
    direct_method = ev.get("best_method") or ("seasonal_naive" if len(total) >= 14 else "sma")
    direct = np.clip(stat_forecast(direct_method, total, horizon), 0, None)
    resid = np.asarray(ev["residuals"].get(direct_method) or np.zeros((0, horizon)))
    qs = per_step_quantiles(resid, coverage) if resid.size else []
    calibrated = len(qs) == horizon and all(q is not None for q in qs)
    cov_eval = backtesting.evaluate_intervals(resid, coverage) if resid.size else {}

    # Both approaches backtested on the group's total over the same origins.
    origins = backtesting.origins_for(len(total), horizon, BACKTEST_FOLDS)
    bt: dict[str, float | None] = {"direct": None, "bottom_up": None}
    if origins:
        acts = np.concatenate([total[o : o + horizon] for o in origins])
        fns = {r["product"].id: _product_forecaster(r["method"] or "") for r in product_rows}
        bu_preds = np.concatenate(
            [
                np.sum(
                    [np.clip(fns[pid](aligned[pid][:o], horizon), 0, None) for pid in fns],
                    axis=0,
                )
                for o in origins
            ]
        )
        d_preds = np.concatenate([np.clip(stat_forecast(direct_method, total[:o], horizon), 0, None) for o in origins])
        bt = {"direct": metrics._r(metrics.wape(acts, d_preds)), "bottom_up": metrics._r(metrics.wape(acts, bu_preds))}
    else:
        warnings.append(f"Need at least {backtesting.MIN_TRAIN + horizon} days of history to compare the two methods")
    if any(r["method"] == "transformer" for r in product_rows):
        warnings.append(
            "Some products use the Transformer; the bottom-up backtest stands in their best statistical method."
        )
    recommended: Reconcile = (
        "top_down"
        if bt["direct"] is not None and bt["bottom_up"] is not None and bt["direct"] < bt["bottom_up"]
        else "bottom_up"
    )
    chosen: Reconcile = reconcile or recommended

    # Reconcile so product numbers add up to the group number on every day.
    if chosen == "bottom_up":
        group = bottom_up
        per_product = [r["points"] for r in product_rows]
    else:
        group = direct
        shares = _daily_shares(product_rows, aligned, bottom_up)
        per_product = [group * s for s in shares]
    lower = upper = None
    if chosen == "top_down" and calibrated:
        q = np.asarray(qs, dtype=float)
        lower, upper = np.clip(group - q, 0, None), group + q

    breakdown = []
    group_sum = float(group.sum())
    for r, pts in zip(product_rows, per_product, strict=True):
        p = r["product"]
        units = float(np.sum(pts))
        breakdown.append(
            {
                "product_id": str(p.id),
                "sku": p.sku,
                "name": p.name,
                "category": p.category,
                "method": r["method"],
                "overrides": r["overrides"],
                "units": round(units, 2),
                "own_forecast_units": round(float(np.sum(r["points"])), 2),
                "share": round(units / group_sum, 4) if group_sum > 0 else 0.0,
            }
        )
    breakdown.sort(key=lambda b: -b["units"])
    hist_from = max(0, len(total) - HISTORY_DAYS)
    return {
        "level": level,
        "category": category,
        "label": label,
        "as_of": as_of.isoformat(),
        "horizon": horizon,
        "coverage": coverage,
        "products": len(products),
        "products_forecast": len(product_rows),
        "excluded": excluded,
        "reconcile": chosen,
        "recommended": recommended,
        "backtest": {"folds": len(origins), "wape": bt},
        "direct_method": direct_method,
        "interval": {
            "calibrated": chosen == "top_down" and calibrated,
            "empirical_coverage": cov_eval.get("empirical_coverage") if chosen == "top_down" else None,
            "note": "Conformal interval from the group model's recent errors."
            if chosen == "top_down" and calibrated
            else "Bottom-up totals have no interval: product intervals cannot simply be added."
            if chosen == "bottom_up"
            else "Not enough history to calibrate an interval.",
        },
        "points": [
            {
                "date": d.isoformat(),
                "yhat": round(float(group[i]), 4),
                "bottom_up": round(float(bottom_up[i]), 4),
                "direct": round(float(direct[i]), 4),
                "lower": None if lower is None else round(float(lower[i]), 4),
                "upper": None if upper is None else round(float(upper[i]), 4),
            }
            for i, d in enumerate(dates)
        ],
        "history": [
            {"date": idx[i].date().isoformat(), "units": round(float(total[i]), 4)}
            for i in range(hist_from, len(total))
        ],
        "breakdown": breakdown,
        "warnings": warnings,
    }


def _daily_shares(rows: list[dict], aligned: dict[uuid.UUID, np.ndarray], bottom_up: np.ndarray) -> list[np.ndarray]:
    """Each product's share of the group per day: from the product forecasts, or recent sales when those are all 0."""
    recent = np.array([float(aligned[r["product"].id][-28:].sum()) for r in rows])
    fallback = recent / recent.sum() if recent.sum() > 0 else np.full(len(rows), 1.0 / len(rows))
    out = []
    for r_i, r in enumerate(rows):
        share = np.where(bottom_up > 0, r["points"] / np.where(bottom_up > 0, bottom_up, 1.0), fallback[r_i])
        out.append(share)
    return out
