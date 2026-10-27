"""Demand-driver explanations computed from the product's own data (no invented causes)."""

from __future__ import annotations

import uuid

import numpy as np
from scipy import stats
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.services import model_registry, series
from app.services.product_service import get_product


def explain(db: Session, org_id: uuid.UUID, product_id: uuid.UUID) -> dict:
    product = get_product(db, org_id, product_id)
    y, imputed = series.load_series(db, org_id, product_id)
    if len(y) < 14:
        raise ValidationFailed("At least 14 days of history are needed to explain demand drivers")
    v = y.values.astype(float)
    recent = y.iloc[-112:]  # last 16 weeks
    drivers = []

    dow = recent.groupby(recent.index.dayofweek).mean()
    weekday = recent[recent.index.dayofweek < 5].mean()
    weekend = recent[recent.index.dayofweek >= 5].mean()
    if weekday > 0 and len(recent) >= 14:
        lift = (weekend - weekday) / weekday
        g1 = recent[recent.index.dayofweek >= 5].values
        g0 = recent[recent.index.dayofweek < 5].values
        p = float(stats.mannwhitneyu(g1, g0).pvalue) if len(g1) > 3 and len(g0) > 3 else None
        drivers.append(
            {
                "driver": "weekend_effect",
                "value": round(float(lift), 4),
                "significant": p is not None and p < 0.05,
                "p_value": None if p is None else round(p, 5),
                "text": f"Weekend days average {abs(lift):.0%} {'more' if lift >= 0 else 'less'} demand than weekdays "
                f"({weekend:.1f} vs {weekday:.1f} units/day over the last {len(recent)} days)"
                + ("" if p is not None and p < 0.05 else "; the difference is not statistically significant")
                + ".",
            }
        )
        peak = int(dow.idxmax())
        names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        drivers.append(
            {
                "driver": "peak_day",
                "value": names[peak],
                "text": f"{names[peak]} is the highest-demand day on average ({dow.max():.1f} units).",
            }
        )

    tail = v[-56:]
    if len(tail) >= 28:
        res = stats.linregress(np.arange(len(tail)), tail)
        weekly = res.slope * 7
        sig = res.pvalue < 0.05
        drivers.append(
            {
                "driver": "trend",
                "value": round(float(weekly), 4),
                "significant": bool(sig),
                "p_value": round(float(res.pvalue), 5),
                "text": (
                    f"Demand is trending {'up' if weekly > 0 else 'down'} by about "
                    f"{abs(weekly):.1f} units/day per week "
                    f"over the last {len(tail)} days."
                    if sig
                    else f"No statistically significant trend over the last {len(tail)} days."
                ),
            }
        )
    if len(v) >= 56:
        last28, prev28 = v[-28:].mean(), v[-56:-28].mean()
        if prev28 > 0:
            ch = (last28 - prev28) / prev28
            drivers.append(
                {
                    "driver": "level_change",
                    "value": round(float(ch), 4),
                    "text": f"The last 4 weeks averaged {last28:.1f} units/day, {abs(ch):.0%} "
                    f"{'higher' if ch >= 0 else 'lower'} than the 4 weeks before ({prev28:.1f}).",
                }
            )
    mean = v[-56:].mean()
    if mean > 0:
        cv = float(v[-56:].std() / mean)
        drivers.append(
            {
                "driver": "volatility",
                "value": round(cv, 4),
                "text": f"Day-to-day variability is {'high' if cv > 0.5 else 'moderate' if cv > 0.25 else 'low'} "
                f"(coefficient of variation {cv:.2f}).",
            }
        )
    zero_share = float((v[-56:] == 0).mean())
    if zero_share > 0.3:
        drivers.append(
            {
                "driver": "intermittency",
                "value": round(zero_share, 4),
                "text": f"{zero_share:.0%} of recent days had zero sales; demand is intermittent.",
            }
        )
    imp_share = float(imputed.iloc[-56:].mean()) if len(imputed) else 0.0
    if imp_share > 0:
        drivers.append(
            {
                "driver": "data_gaps",
                "value": round(imp_share, 4),
                "text": f"{imp_share:.0%} of the last 56 days were missing from the data and filled using the "
                f"'{product.missing_policy}' policy.",
            }
        )

    importance = None
    active = model_registry.get_active(db, org_id)
    if active is not None and product.sku in active.training_meta.get("products", []):
        importance = active.metrics.get("importance")
    return {
        "product_id": str(product_id),
        "sku": product.sku,
        "as_of": y.index[-1].date().isoformat(),
        "drivers": drivers,
        "model_feature_importance": importance,
        "method_note": "Drivers are descriptive statistics of this product's sales history. Feature importance, when "
        "shown, is permutation importance of the active Transformer (not SHAP) and is global "
        "across products. No causal claims are made.",
    }
