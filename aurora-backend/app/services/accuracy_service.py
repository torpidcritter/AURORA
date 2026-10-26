"""Live forecast accuracy: stored forecasts compared with the sales that later arrived.

Validation metrics are measured once, on held-out history, when a model is trained. This module measures what the
forecasts actually served to users scored once the real demand was known, so a model that degrades after promotion is
noticed.

For each product and day, the forecast scored is the freshest one made before that day (largest `as_of`, latest
stored when several share it), which is what a planner would have been looking at. Only days up to the product's
last uploaded sale are scored, so data that has not arrived yet does not count as zero demand. MAPE skips days with
zero actual demand (it is undefined there) and reports how many were skipped; WAPE covers every day.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ForecastRun, Product, SalesRecord
from app.ml import metrics
from app.services import series

MIN_POINTS = 14


def live_accuracy(db: Session, org_id: uuid.UUID, window_days: int = 28) -> dict:
    last_sale = dict(
        db.execute(
            select(SalesRecord.product_id, func.max(SalesRecord.sale_date))
            .where(SalesRecord.org_id == org_id)
            .group_by(SalesRecord.product_id)
        )
        .tuples()
        .all()
    )
    if not last_sale:
        return _empty(window_days)
    newest = max(last_sale.values())
    start = newest - timedelta(days=window_days - 1)
    runs = db.execute(
        select(ForecastRun.product_id, ForecastRun.method, ForecastRun.payload, ForecastRun.created_at)
        .where(
            ForecastRun.org_id == org_id,
            # a run can only cover the window if it was made less than (window + longest horizon) days ago
            ForecastRun.created_at >= datetime.now(UTC) - timedelta(days=window_days + 120),
        )
        .order_by(ForecastRun.created_at)
    ).all()

    # (product, day) -> (as_of, created_at, yhat, method); keep the freshest forecast made before that day
    best: dict[tuple[uuid.UUID, date], tuple[date, datetime, float, str]] = {}
    for pid, method, payload, created in runs:
        try:
            as_of = date.fromisoformat(payload["as_of"])
            points = payload["points"]
        except (KeyError, TypeError, ValueError):
            continue
        cutoff = last_sale.get(pid)
        if cutoff is None:
            continue
        for p in points:
            d = date.fromisoformat(p["date"])
            if d < start or d > cutoff or d <= as_of:
                continue
            cur = best.get((pid, d))
            if cur is None or (as_of, created) > (cur[0], cur[1]):
                best[(pid, d)] = (as_of, created, float(p["yhat"]), method)
    if not best:
        return _empty(window_days)

    actuals = series.load_many(db, org_id, list({pid for pid, _ in best}))
    per_product: dict[uuid.UUID, tuple[list[float], list[float]]] = defaultdict(lambda: ([], []))
    per_method: dict[str, tuple[list[float], list[float]]] = defaultdict(lambda: ([], []))
    for (pid, d), (_, _, yhat, method) in best.items():
        s = actuals.get(pid)
        if s is None:
            continue
        actual = s.get(pd.Timestamp(d))
        if actual is None:
            continue
        for bucket in (per_product[pid], per_method[method]):
            bucket[0].append(float(actual))
            bucket[1].append(yhat)

    skus = dict(db.execute(select(Product.id, Product.sku).where(Product.org_id == org_id)).tuples().all())
    y_all = [v for ys, _ in per_product.values() for v in ys]
    f_all = [v for _, fs in per_product.values() for v in fs]
    overall = _score(y_all, f_all)
    return {
        "window_days": window_days,
        "window_start": start.isoformat(),
        "window_end": newest.isoformat(),
        "sufficient": overall["points"] >= MIN_POINTS,
        "min_points": MIN_POINTS,
        **overall,
        "by_method": {m: _score(ys, fs) for m, (ys, fs) in sorted(per_method.items())},
        "by_product": sorted(
            ({"product_id": str(pid), "sku": skus.get(pid), **_score(ys, fs)} for pid, (ys, fs) in per_product.items()),
            key=lambda r: (r["wape"] is None, -(r["wape"] or 0)),
        ),
    }


def _score(y: list[float], f: list[float]) -> dict:
    if not y:
        return {"points": 0, "wape": None, "mape": None, "mape_excluded_zero_days": 0}
    m, excluded = metrics.mape(y, f)
    w = metrics.wape(y, f)
    return {
        "points": len(y),
        "wape": None if w is None else round(w, 2),
        "mape": None if m is None else round(m, 2),
        "mape_excluded_zero_days": excluded,
    }


def _empty(window_days: int) -> dict:
    return {
        "window_days": window_days,
        "window_start": None,
        "window_end": None,
        "sufficient": False,
        "min_points": MIN_POINTS,
        "points": 0,
        "wape": None,
        "mape": None,
        "mape_excluded_zero_days": 0,
        "by_method": {},
        "by_product": [],
    }
