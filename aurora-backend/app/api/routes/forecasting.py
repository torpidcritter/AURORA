"""Forecasting, inventory intelligence, risk, simulation and external-signal endpoints."""

from __future__ import annotations

import uuid

import numpy as np
from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, require
from app.api.routes.data import _read_limited
from app.core import rate_limit
from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.core.security import Perm
from app.db.models import ExternalSignal, ForecastRun
from app.db.session import get_db
from app.ml import backtesting, drift
from app.ml.statistical import METHOD_INFO
from app.schemas.forecast import BacktestIn, ForecastIn, InventoryIn, RiskBatchIn, ScenarioIn, SignalIn
from app.services import (
    audit,
    explain_service,
    forecast_service,
    inventory_service,
    scenario_service,
    series,
    signal_service,
)
from app.services.forecast_service import ForecastRequest
from app.services.product_service import get_product

router = APIRouter(tags=["forecasting"])


def _throttle(p: Principal, bucket: str, per_minute: int) -> None:
    rate_limit.hit(f"{bucket}:{p.user_id}", per_minute, 60)


@router.post("/forecast")
def forecast(body: ForecastIn, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    _throttle(p, "forecast", 60)
    res = forecast_service.generate(db, p.org_id, ForecastRequest(**body.model_dump()))
    db.commit()
    return res


@router.get("/forecast/methods")
def methods(p: Principal = Depends(require(Perm.read))) -> dict:
    return {
        "methods": [{"name": k, "description": v} for k, v in METHOD_INFO.items()],
        "auto": "Picks the Transformer only where it beat every baseline for that product on held-out data; "
        "otherwise the baseline with the lowest rolling-origin WAPE.",
        "coverages": list(forecast_service.SUPPORTED_COVERAGES),
        "max_horizon": forecast_service.MAX_HORIZON,
        "transformer_horizons": [7, 14, 28, 56],
    }


@router.get("/forecast/explain/{product_id}")
def explain(product_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    return explain_service.explain(db, p.org_id, product_id)


@router.get("/forecast/history/{product_id}")
def forecast_history(
    product_id: uuid.UUID,
    limit: int = Query(10, ge=1, le=50),
    p: Principal = Depends(require(Perm.read)),
    db: Session = Depends(get_db),
) -> list[dict]:
    get_product(db, p.org_id, product_id)
    runs = db.scalars(
        select(ForecastRun)
        .where(ForecastRun.product_id == product_id, ForecastRun.org_id == p.org_id)
        .order_by(ForecastRun.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": str(r.id),
            "created_at": r.created_at.isoformat(),
            "method": r.method,
            "horizon": r.horizon,
            "as_of": r.payload.get("as_of"),
            "points": r.payload.get("points"),
        }
        for r in runs
    ]


@router.get("/forecast/latest")
def latest_forecasts(
    limit: int = Query(200, ge=1, le=500), p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> dict:
    """Most recent stored forecast per product (one row each)."""
    from sqlalchemy import func as sa_func

    from app.db.models import Product

    latest = (
        select(ForecastRun.product_id, sa_func.max(ForecastRun.created_at).label("ts"))
        .where(ForecastRun.org_id == p.org_id)
        .group_by(ForecastRun.product_id)
        .subquery()
    )
    rows = db.execute(
        select(ForecastRun, Product.sku, Product.name)
        .join(latest, (latest.c.product_id == ForecastRun.product_id) & (latest.c.ts == ForecastRun.created_at))
        .join(Product, Product.id == ForecastRun.product_id)
        .order_by(Product.sku)
        .limit(limit)
    ).all()
    items = []
    for run, sku, name in rows:
        pts = run.payload.get("points", [])
        items.append(
            {
                "product_id": str(run.product_id),
                "sku": sku,
                "name": name,
                "method": run.method,
                "horizon": run.horizon,
                "coverage": run.coverage,
                "created_at": run.created_at.isoformat(),
                "as_of": run.payload.get("as_of"),
                "next_7d_units": round(sum(x["yhat"] for x in pts[:7]), 2),
                "interval_calibrated": run.payload.get("interval", {}).get("calibrated"),
                "empirical_coverage": run.payload.get("interval", {}).get("empirical_coverage"),
            }
        )
    return {"items": items}


@router.get("/intelligence/demand-trend")
def demand_trend(
    days: int = Query(90, ge=7, le=730), p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> dict:
    """Organisation-wide units sold per day (observed rows only) for the last `days` days ending at the latest sale."""
    from datetime import timedelta

    from sqlalchemy import func as sa_func

    from app.db.models import SalesRecord
    from app.services.product_service import org_as_of

    as_of = org_as_of(db, p.org_id)
    if as_of is None:
        return {"as_of": None, "points": []}
    start = as_of - timedelta(days=days - 1)
    rows = db.execute(
        select(SalesRecord.sale_date, sa_func.sum(SalesRecord.quantity))
        .where(SalesRecord.org_id == p.org_id, SalesRecord.sale_date >= start)
        .group_by(SalesRecord.sale_date)
        .order_by(SalesRecord.sale_date)
    ).all()
    by_day = {d: float(q) for d, q in rows}
    pts = [
        {"date": (start + timedelta(days=i)).isoformat(), "units": round(by_day.get(start + timedelta(days=i), 0.0), 2)}
        for i in range(days)
    ]
    return {"as_of": as_of.isoformat(), "points": pts}


@router.post("/inventory/recommendation")
def inventory_recommendation(
    body: InventoryIn, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> dict:
    overrides = body.model_dump(exclude={"product_id"}, exclude_none=True)
    res = inventory_service.recommendation(db, p.org_id, body.product_id, overrides=overrides)
    db.commit()
    return res


@router.post("/risk/stockout")
def stockout(body: InventoryIn, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    res = inventory_service.recommendation(
        db, p.org_id, body.product_id, overrides=body.model_dump(exclude={"product_id"}, exclude_none=True)
    )
    db.commit()
    keys = (
        "product_id",
        "sku",
        "name",
        "status",
        "risk_level",
        "urgency",
        "urgency_components",
        "days_until_stockout",
        "stockout_probability_lead_time",
        "lead_time_days",
        "on_hand",
        "reorder_point",
        "recommended_order_qty",
        "narrative",
        "message",
        "assumptions",
    )
    return {k: res.get(k) for k in keys if k in res}


@router.post("/risk/stockout/batch")
def stockout_batch(
    body: RiskBatchIn, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> dict:
    _throttle(p, "risk-batch", 20)
    rows = inventory_service.risk_matrix(db, p.org_id, product_ids=body.product_ids)
    db.commit()
    return {"items": rows, "total": len(rows)}


@router.get("/intelligence/kpi")
def kpi(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    _throttle(p, "kpi", 30)
    res = inventory_service.kpis(db, p.org_id)
    db.commit()
    return res


@router.get("/intelligence/recommendations")
def recommendations(
    limit: int = Query(20, ge=1, le=100), p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> dict:
    items = inventory_service.recommendations(db, p.org_id, limit)
    db.commit()
    return {"items": items}


@router.post("/intelligence/backtest")
def backtest(body: BacktestIn, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    _throttle(p, "backtest", 20)
    get_product(db, p.org_id, body.product_id)
    y, _ = series.load_series(db, p.org_id, body.product_id)
    if len(y) < 14 + body.horizon:
        raise ValidationFailed(f"Need at least {14 + body.horizon} days of history for this horizon")
    ev = forecast_service.baseline_evaluation(db, p.org_id, body.product_id, y, body.horizon)
    coverage = {}
    for m, resid in ev["residuals"].items():
        arr = np.asarray(resid)
        coverage[m] = {str(c): backtesting.evaluate_intervals(arr, c) for c in forecast_service.SUPPORTED_COVERAGES}
    audit.record_for(
        db,
        p,
        "forecast.backtest",
        resource_type="product",
        resource_id=body.product_id,
        details={"horizon": body.horizon},
    )
    db.commit()
    return {
        "product_id": str(body.product_id),
        "horizon": body.horizon,
        "as_of": y.index[-1].date().isoformat(),
        "summary": forecast_service._backtest_summary(ev),
        "interval_coverage": coverage,
        "intermittent": ev["intermittent"],
        "history_days": ev["history_days"],
    }


@router.get("/intelligence/drift")
def drift_all(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    s = get_settings()
    out = []
    from app.db.models import Product

    skus = dict(db.execute(select(Product.id, Product.sku).where(Product.org_id == p.org_id)).tuples().all())
    for pid, y in series.load_many(db, p.org_id).items():
        r = drift.detect(y.values, ks_threshold=s.drift_ks_threshold, mean_threshold=s.drift_mean_change_threshold)
        out.append({"product_id": str(pid), "sku": skus.get(pid), **r})
    out.sort(key=lambda r: (not r["drift"], r.get("sku") or ""))
    return {"items": out, "drifting": sum(1 for r in out if r["drift"])}


@router.get("/simulate/types")
def scenario_types(p: Principal = Depends(require(Perm.read))) -> dict:
    return {"types": scenario_service.SCENARIO_TYPES}


@router.post("/simulate/scenario")
def simulate(body: ScenarioIn, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    _throttle(p, "simulate", 60)
    res = scenario_service.simulate(
        db, p.org_id, body.product_id, [a.model_dump() for a in body.adjustments], body.horizon
    )
    db.commit()
    return res


def _signal_out(s: ExternalSignal) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "signal_type": s.signal_type,
        "start_date": s.start_date.isoformat(),
        "end_date": s.end_date.isoformat(),
        "magnitude": s.magnitude,
        "product_id": str(s.product_id) if s.product_id else None,
    }


@router.get("/signals")
def list_signals(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(ExternalSignal)
        .where(ExternalSignal.org_id == p.org_id)
        .order_by(ExternalSignal.start_date.desc())
        .limit(500)
    ).all()
    return [_signal_out(s) for s in rows]


@router.post("/signals", status_code=201)
def create_signal(
    body: SignalIn, p: Principal = Depends(require(Perm.manage_signals)), db: Session = Depends(get_db)
) -> dict:
    s = signal_service.create(db, p.org_id, p.user_id, **body.model_dump())
    audit.record_for(
        db, p, "signal.create", resource_type="signal", resource_id=s.id, details=body.model_dump(mode="json")
    )
    db.commit()
    return _signal_out(s)


@router.post("/signals/upload")
async def upload_signals(
    file: UploadFile = File(...), p: Principal = Depends(require(Perm.manage_signals)), db: Session = Depends(get_db)
) -> dict:
    res = signal_service.import_csv(db, p.org_id, p.user_id, await _read_limited(file))
    audit.record_for(db, p, "signal.upload", resource_type="signal", details={"created": res["created"]})
    db.commit()
    return res


@router.delete("/signals/{signal_id}", status_code=204)
def delete_signal(
    signal_id: uuid.UUID, p: Principal = Depends(require(Perm.manage_signals)), db: Session = Depends(get_db)
) -> None:
    signal_service.delete(db, p.org_id, signal_id)
    audit.record_for(db, p, "signal.delete", resource_type="signal", resource_id=signal_id)
    db.commit()
