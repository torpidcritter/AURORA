"""Forecast generation, method selection and baseline evaluation (with caching)."""

from __future__ import annotations

import hashlib
import threading
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.core.metrics import FORECASTS_SERVED
from app.db.models import BacktestResult, ExternalSignal, ForecastRun, Product
from app.ml import backtesting
from app.ml.conformal import per_step_quantiles
from app.ml.statistical import METHODS, is_intermittent
from app.ml.statistical import forecast as stat_forecast
from app.services import model_registry
from app.services import series as series_svc
from app.services.product_service import get_product

SUPPORTED_COVERAGES = (0.8, 0.9, 0.95)
STAT_METHODS = ("sma", "ses", "holt_winters", "seasonal_naive", "croston_sba")
FAST_METHODS = ("sma", "ses", "seasonal_naive", "croston_sba")
MAX_HORIZON = 90
BACKTEST_FOLDS = 4
CAL_ORIGINS = 42

_inference_lock = threading.Lock()  # MC dropout toggles module state; serialise use of shared models


def fingerprint(y: pd.Series) -> str:
    if y.empty:
        return "empty"
    h = hashlib.sha256()
    h.update(str(y.index[0].date()).encode())
    h.update(str(y.index[-1].date()).encode())
    h.update(np.round(y.values, 4).tobytes())
    return h.hexdigest()[:32]


def baseline_evaluation(
    db: Session | None, org_id: uuid.UUID, product_id: uuid.UUID, y: pd.Series, horizon: int, fast: bool = False
) -> dict:
    """Rolling-origin backtest (K=4) for metrics + dense-origin residuals for conformal calibration.
    Cached in backtest_results by data fingerprint so repeated calls are cheap."""
    fp = fingerprint(y) + ("-fast" if fast else "")
    if db is not None:
        cached = db.scalar(
            select(BacktestResult)
            .where(
                BacktestResult.product_id == product_id,
                BacktestResult.horizon == horizon,
                BacktestResult.org_id == org_id,
            )
            .order_by(BacktestResult.created_at.desc())
            .limit(1)
        )
        if cached is not None and cached.payload.get("fingerprint") in (fp, fp.removesuffix("-fast")):
            return cached.payload
    methods = FAST_METHODS if fast else STAT_METHODS
    values = y.values.astype(float)
    fns = backtesting.stat_forecasters(methods)
    bt = backtesting.rolling_origin(values, horizon, BACKTEST_FOLDS, fns)
    residuals = {
        m: backtesting.calibration_residuals(
            values, fns[m], horizon, n_origins=CAL_ORIGINS if not fast else 21
        ).tolist()
        for m in methods
    }
    payload = {
        "fingerprint": fp,
        "horizon": horizon,
        "folds": BACKTEST_FOLDS,
        "backtest": bt,
        "best_method": bt.get("best_method"),
        "residuals": residuals,
        "history_days": len(values),
        "intermittent": is_intermittent(values),
    }
    if db is not None:
        db.add(
            BacktestResult(
                org_id=org_id,
                product_id=product_id,
                horizon=horizon,
                folds=BACKTEST_FOLDS,
                best_method=bt.get("best_method"),
                payload=payload,
            )
        )
        db.flush()
    return payload


def _fallback_method(y: np.ndarray) -> str:
    if is_intermittent(y):
        return "croston_sba"
    return "seasonal_naive" if len(y) >= 14 else "sma"


@dataclass
class ForecastRequest:
    product_id: uuid.UUID
    horizon: int = 7
    method: str = "auto"
    coverage: float = 0.9
    mc_samples: int = 20
    persist: bool = True
    fast: bool = False


def generate(db: Session, org_id: uuid.UUID, req: ForecastRequest) -> dict:
    if not 1 <= req.horizon <= MAX_HORIZON:
        raise ValidationFailed(f"horizon must be between 1 and {MAX_HORIZON}")
    if req.coverage not in SUPPORTED_COVERAGES:
        raise ValidationFailed(f"coverage must be one of {SUPPORTED_COVERAGES}")
    if req.method not in ("auto", "transformer", *STAT_METHODS):
        raise ValidationFailed("unknown method")
    product = get_product(db, org_id, req.product_id)
    y, imputed = series_svc.load_series(db, org_id, product.id)
    if y.empty:
        raise ValidationFailed("This product has no sales history yet; upload data first")
    as_of: date = y.index[-1].date()
    values = y.values.astype(float)
    warnings: list[str] = []
    if len(values) < 28:
        warnings.append(f"Only {len(values)} days of history; forecasts and intervals are low-confidence")
    if imputed.iloc[-30:].mean() > 0.3:
        warnings.append("More than 30% of the last 30 days were imputed (missing in the data)")

    active = model_registry.get_active(db, org_id)
    bundle = model = None
    transformer_ok, transformer_reason = False, "no active Transformer model"
    if active is not None:
        bundle, model = model_registry.load(active)
        if product.sku not in bundle["products"]:
            transformer_reason = "product was not in the active model's training set"
        elif req.horizon > bundle["config"]["horizon"]:
            transformer_reason = f"active model was trained for a {bundle['config']['horizon']}-day horizon"
        elif len(values) < bundle["config"]["window"]:
            transformer_reason = "not enough recent history for the model's input window"
        else:
            transformer_ok, transformer_reason = True, "available"

    method = req.method
    reason = "requested explicitly"
    evaluation: dict | None = None
    if method == "transformer" and not transformer_ok:
        raise ValidationFailed(f"Transformer unavailable for this product: {transformer_reason}")
    if method == "auto" or method in STAT_METHODS:
        evaluation = baseline_evaluation(db, org_id, product.id, y, req.horizon, fast=req.fast)
    if method == "auto":
        pp = (bundle or {}).get("metrics", {}).get("per_product", {}).get(product.sku) if transformer_ok else None
        if pp and pp.get("transformer_beats_best_baseline"):
            method, reason = "transformer", "Transformer beat every baseline for this product on held-out data"
        elif evaluation and evaluation.get("best_method"):
            method = str(evaluation["best_method"])
            reason = f"lowest WAPE in a {BACKTEST_FOLDS}-fold rolling-origin backtest"
            if transformer_ok:
                reason += "; Transformer did not beat the best baseline for this product"
        else:
            method = _fallback_method(values)
            reason = "too little history to backtest; rule-based fallback"
    if method not in ("transformer", *METHODS):
        method = _fallback_method(values)

    dates = [as_of + timedelta(days=i + 1) for i in range(req.horizon)]
    epistemic = None
    if method == "transformer":
        assert bundle is not None and model is not None
        from app.ml.training import predict

        with _inference_lock:
            tf = predict(bundle, model, product.sku, values, pd.DatetimeIndex(y.index), mc_samples=req.mc_samples)
        point = tf.mean[: req.horizon]
        qs = bundle["conformal"].get(product.sku, {}).get(str(req.coverage), [])[: req.horizon]
        cov_eval = bundle["metrics"]["interval_coverage"]["per_product"].get(product.sku, {}).get(str(req.coverage), {})
        if req.mc_samples > 0:
            epistemic = {
                "method": "mc_dropout",
                "samples": req.mc_samples,
                "std": [round(float(v), 4) for v in tf.mc_std[: req.horizon]],
                "note": "Spread of predictions with dropout active: a heuristic signal of model (epistemic) "
                "uncertainty, not a calibrated prediction interval.",
            }
        model_version = active.version if active else None
        model_version_id = active.id if active else None
        error_sd = (bundle["metrics"]["per_product"].get(product.sku, {}).get("transformer", {}) or {}).get("rmse")
        error_sd_source = "Transformer RMSE on held-out evaluation windows"
    else:
        point = stat_forecast(method, values, req.horizon)
        assert evaluation is not None or method in METHODS
        if evaluation is None:
            evaluation = baseline_evaluation(db, org_id, product.id, y, req.horizon, fast=req.fast)
        resid = np.asarray(evaluation["residuals"].get(method) or np.zeros((0, req.horizon)))
        if resid.size == 0 or resid.ndim != 2:
            # method not in the cached residual set (e.g. holt_winters under fast mode): compute now
            resid = backtesting.calibration_residuals(
                values, backtesting.stat_forecasters([method])[method], req.horizon
            )
        qs = per_step_quantiles(resid, req.coverage) if resid.size else []
        cov_eval = backtesting.evaluate_intervals(resid, req.coverage) if resid.size else {}
        error_sd = float(np.sqrt(np.mean(resid**2))) if resid.size else None
        error_sd_source = f"RMSE of {method} forecast errors over recent rolling origins"
        model_version = None
        model_version_id = None

    if error_sd is None or not np.isfinite(error_sd):
        recent = values[-56:]
        error_sd = float(np.std(recent)) if len(recent) > 1 else 0.0
        error_sd_source = "standard deviation of the last 56 days of demand (no forecast-error history)"
    calibrated = len(qs) == req.horizon and all(q is not None for q in qs)
    points = []
    for i, d in enumerate(dates):
        yhat = float(max(point[i], 0.0))
        lo = hi = None
        if calibrated:
            lo, hi = max(yhat - float(qs[i]), 0.0), yhat + float(qs[i])  # type: ignore[arg-type]
        points.append(
            {
                "date": d.isoformat(),
                "yhat": round(yhat, 4),
                "lower": None if lo is None else round(lo, 4),
                "upper": None if hi is None else round(hi, 4),
            }
        )
    interval = {
        "method": "split_conformal_abs_residual",
        "coverage": req.coverage,
        "calibrated": calibrated,
        "empirical_coverage": cov_eval.get("empirical_coverage"),
        "n_calibration": cov_eval.get("n_calibration"),
        "n_test": cov_eval.get("n_test"),
        "note": "Intervals are conformal prediction intervals from recent forecast errors. Coverage is measured on "
        "held-out recent periods and is not guaranteed if demand behaviour changes."
        if calibrated
        else "Not enough history to calibrate intervals at this coverage level.",
    }
    signals = db.scalars(
        select(ExternalSignal).where(
            ExternalSignal.org_id == org_id,
            ExternalSignal.start_date <= dates[-1],
            ExternalSignal.end_date >= dates[0],
            (ExternalSignal.product_id.is_(None)) | (ExternalSignal.product_id == product.id),
        )
    ).all()
    if signals:
        warnings.append(
            f"{len(signals)} external signal(s) overlap this horizon. They are not applied to the statistical "
            "forecast automatically; use a scenario to model their effect."
        )
    result = {
        "product_id": str(product.id),
        "sku": product.sku,
        "as_of": as_of.isoformat(),
        "horizon": req.horizon,
        "method": method,
        "method_reason": reason,
        "transformer_status": transformer_reason,
        "model_version": model_version,
        "points": points,
        "interval": interval,
        "epistemic_uncertainty": epistemic,
        "error_sd": round(float(error_sd), 4),
        "error_sd_source": error_sd_source,
        "backtest": _backtest_summary(evaluation),
        "signals": [
            {
                "id": str(s.id),
                "name": s.name,
                "type": s.signal_type,
                "start_date": s.start_date.isoformat(),
                "end_date": s.end_date.isoformat(),
                "magnitude": s.magnitude,
            }
            for s in signals
        ],
        "warnings": warnings,
    }
    FORECASTS_SERVED.labels(method).inc()
    if req.persist:
        run = ForecastRun(
            org_id=org_id,
            product_id=product.id,
            model_version_id=model_version_id,
            method=method,
            horizon=req.horizon,
            coverage=req.coverage,
            payload=result,
        )
        db.add(run)
        db.flush()
        result["forecast_id"] = str(run.id)
    return result


def _backtest_summary(ev: dict | None) -> dict | None:
    if not ev:
        return None
    bt = ev.get("backtest", {})
    return {
        "folds": ev.get("folds"),
        "origins": bt.get("origins"),
        "best_method": ev.get("best_method"),
        "error": bt.get("error"),
        "methods": {m: {k: v for k, v in r.items() if k != "folds"} for m, r in (bt.get("methods") or {}).items()},
    }


def latest_forecast(db: Session, org_id: uuid.UUID, product: Product, y: pd.Series, horizon: int) -> dict | None:
    """Most recent stored forecast that is still based on the latest data (same as_of)."""
    if y.empty:
        return None
    run = db.scalar(
        select(ForecastRun)
        .where(ForecastRun.product_id == product.id, ForecastRun.org_id == org_id, ForecastRun.horizon >= horizon)
        .order_by(ForecastRun.created_at.desc())
        .limit(1)
    )
    if run is None or run.payload.get("as_of") != y.index[-1].date().isoformat():
        return None
    return run.payload
