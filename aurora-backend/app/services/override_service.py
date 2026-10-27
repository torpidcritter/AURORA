"""Planner overrides of the statistical forecast, and whether they helped.

An override sets demand to a fixed number of units per day, or scales the model's forecast, for a date range. It is
applied when forecasts are used for decisions (reorder maths, purchase order suggestions, the forecast view). The
stored model forecast is never changed, so afterwards both can be scored against actual sales: the forecast value
added (FVA) of the override is how much it reduced the error compared with the model alone.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import ForecastOverride, Product, SalesRecord
from app.ml import metrics
from app.services import accuracy_service, series
from app.services.product_service import get_product

MAX_DAYS = 366
MAX_SCALE = 10.0
MAX_UNITS = 10_000_000.0


def _now() -> datetime:
    return datetime.now(UTC)


def active_for(db: Session, org_id: uuid.UUID, product_id: uuid.UUID, start: date, end: date) -> list[ForecastOverride]:
    return list(
        db.scalars(
            select(ForecastOverride)
            .where(
                ForecastOverride.org_id == org_id,
                ForecastOverride.product_id == product_id,
                ForecastOverride.archived_at.is_(None),
                ForecastOverride.start_date <= end,
                ForecastOverride.end_date >= start,
            )
            .order_by(ForecastOverride.start_date)
        )
    )


def adjusted_value(o: ForecastOverride, model_yhat: float) -> float:
    return float(o.value) if o.kind == "set" else float(model_yhat) * float(o.value)


def adjust_points(
    db: Session, org_id: uuid.UUID, product_id: uuid.UUID, points: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Apply active overrides to forecast points ({"date", "yhat", ...}).

    Returns the adjusted points (each keeps the model's value as ``yhat_model`` where it changed) and one summary per
    override that touched the horizon. Interval bounds of an adjusted day are dropped: they describe the model's
    error, not the planner's.
    """
    if not points:
        return points, []
    start, end = date.fromisoformat(points[0]["date"]), date.fromisoformat(points[-1]["date"])
    overrides = active_for(db, org_id, product_id, start, end)
    if not overrides:
        return points, []
    out, touched = [], {}
    for p in points:
        d = date.fromisoformat(p["date"])
        o = next((o for o in overrides if o.start_date <= d <= o.end_date), None)
        if o is None:
            out.append(p)
            continue
        out.append(
            {**p, "yhat": round(adjusted_value(o, p["yhat"]), 4), "yhat_model": p["yhat"], "lower": None, "upper": None}
        )
        t = touched.setdefault(o.id, {"id": str(o.id), "kind": o.kind, "value": o.value, "reason": o.reason, "days": 0})
        t["days"] += 1
    return out, list(touched.values())


def create(
    db: Session,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    product_id: uuid.UUID,
    start_date: date,
    end_date: date,
    kind: str,
    value: float,
    reason: str,
) -> ForecastOverride:
    get_product(db, org_id, product_id)
    if end_date < start_date:
        raise ValidationFailed("end_date must be on or after start_date")
    if (end_date - start_date).days + 1 > MAX_DAYS:
        raise ValidationFailed(f"An override can cover at most {MAX_DAYS} days")
    if kind == "scale" and not 0 <= value <= MAX_SCALE:
        raise ValidationFailed(f"A scale factor must be between 0 and {MAX_SCALE:g}")
    if kind == "set" and not 0 <= value <= MAX_UNITS:
        raise ValidationFailed("Units per day must be between 0 and 10,000,000")
    if not reason.strip():
        raise ValidationFailed("Give a reason, so the override can be reviewed later")
    # Lock the product so two planners cannot add overlapping overrides at the same time.
    db.execute(select(Product.id).where(Product.id == product_id, Product.org_id == org_id).with_for_update())
    clash = active_for(db, org_id, product_id, start_date, end_date)
    if clash:
        c = clash[0]
        raise Conflict(
            f"This product already has an override from {c.start_date} to {c.end_date}. "
            "Archive it or pick other dates.",
            code="override_overlap",
        )
    o = ForecastOverride(
        org_id=org_id,
        product_id=product_id,
        start_date=start_date,
        end_date=end_date,
        kind=kind,
        value=float(value),
        reason=reason.strip()[:500],
        created_by=user_id,
    )
    db.add(o)
    db.flush()
    return o


def get(db: Session, org_id: uuid.UUID, override_id: uuid.UUID) -> ForecastOverride:
    o = db.get(ForecastOverride, override_id)
    if o is None or o.org_id != org_id:
        raise NotFound("Override not found")
    return o


def archive(db: Session, org_id: uuid.UUID, override_id: uuid.UUID) -> ForecastOverride:
    """Stop applying an override. It is kept, so its accuracy can still be reviewed."""
    o = get(db, org_id, override_id)
    if o.archived_at is None:
        o.archived_at = _now()
    db.flush()
    return o


def list_overrides(
    db: Session,
    org_id: uuid.UUID,
    *,
    product_id: uuid.UUID | None = None,
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    q = select(ForecastOverride, Product.sku, Product.name).join(Product, Product.id == ForecastOverride.product_id)
    q = q.where(ForecastOverride.org_id == org_id)
    if product_id is not None:
        q = q.where(ForecastOverride.product_id == product_id)
    if not include_archived:
        q = q.where(ForecastOverride.archived_at.is_(None))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.execute(q.order_by(ForecastOverride.start_date.desc()).limit(limit).offset(offset)).all()
    return [to_out(o, sku, name) for o, sku, name in rows], total


def to_out(o: ForecastOverride, sku: str | None = None, name: str | None = None) -> dict:
    return {
        "id": str(o.id),
        "product_id": str(o.product_id),
        "sku": sku,
        "name": name,
        "start_date": o.start_date.isoformat(),
        "end_date": o.end_date.isoformat(),
        "kind": o.kind,
        "value": o.value,
        "reason": o.reason,
        "created_by": str(o.created_by) if o.created_by else None,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "archived_at": o.archived_at.isoformat() if o.archived_at else None,
    }


def value_added(db: Session, org_id: uuid.UUID, window_days: int = 180) -> dict:
    """Score every override (archived ones too) on the days it covered that now have actual sales.

    For each covered day: the model's forecast (the freshest made before that day, as in live accuracy), the
    adjusted value, and the actual. FVA = model WAPE minus override WAPE, in percentage points: positive means the
    override helped.
    """
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
        return {"window_days": window_days, "overrides": [], "overall": None}
    newest = max(last_sale.values())
    start = newest - timedelta(days=window_days - 1)
    overrides = list(
        db.scalars(
            select(ForecastOverride).where(
                ForecastOverride.org_id == org_id,
                ForecastOverride.end_date >= start,  # future ones are listed as pending
            )
        )
    )
    if not overrides:
        return {"window_days": window_days, "overrides": [], "overall": None}
    served = accuracy_service.served_forecasts(db, org_id, start, last_sale)
    pids = list({o.product_id for o in overrides})
    actuals = series.load_many(db, org_id, pids)
    skus = dict(db.execute(select(Product.id, Product.sku).where(Product.id.in_(pids))).tuples().all())
    rows, all_y, all_model, all_adj = [], [], [], []
    for o in sorted(overrides, key=lambda o: o.start_date, reverse=True):
        y, f_model, f_adj = [], [], []
        s = actuals.get(o.product_id)
        d = max(o.start_date, start)
        stop = min(o.end_date, last_sale.get(o.product_id, newest))
        while s is not None and d <= stop:
            model = served.get((o.product_id, d))
            actual = s.get(pd.Timestamp(d))
            if model is not None and actual is not None:
                y.append(float(actual))
                f_model.append(model[0])
                f_adj.append(adjusted_value(o, model[0]))
            d += timedelta(days=1)
        row = {**to_out(o, skus.get(o.product_id)), **_fva(y, f_model, f_adj)}
        rows.append(row)
        all_y += y
        all_model += f_model
        all_adj += f_adj
    return {"window_days": window_days, "overrides": rows, "overall": _fva(all_y, all_model, all_adj)}


def _fva(y: list[float], model: list[float], adjusted: list[float]) -> dict:
    if not y:
        return {"scored_days": 0, "model_wape": None, "override_wape": None, "fva_points": None, "verdict": "pending"}
    mw, ow = metrics.wape(y, model), metrics.wape(y, adjusted)
    fva = None if mw is None or ow is None else round(mw - ow, 2)
    verdict = "pending" if fva is None else "helped" if fva > 0.5 else "hurt" if fva < -0.5 else "no_change"
    return {
        "scored_days": len(y),
        "model_wape": None if mw is None else round(mw, 2),
        "override_wape": None if ow is None else round(ow, 2),
        "fva_points": fva,
        "verdict": verdict,
    }
