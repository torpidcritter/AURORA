"""Forecast accuracy metrics with explicit zero handling."""

from __future__ import annotations

import math

import numpy as np


def _arr(x: object) -> np.ndarray:
    return np.asarray(x, dtype=float).ravel()


def mae(y: object, yhat: object) -> float:
    y, yhat = _arr(y), _arr(yhat)
    return float(np.mean(np.abs(y - yhat))) if y.size else math.nan


def rmse(y: object, yhat: object) -> float:
    y, yhat = _arr(y), _arr(yhat)
    return float(np.sqrt(np.mean((y - yhat) ** 2))) if y.size else math.nan


def wape(y: object, yhat: object) -> float | None:
    """100 * sum|y - yhat| / sum|y|. Undefined (None) when every actual is zero."""
    y, yhat = _arr(y), _arr(yhat)
    denom = np.sum(np.abs(y))
    if denom == 0:
        return None
    return float(100 * np.sum(np.abs(y - yhat)) / denom)


def mape(y: object, yhat: object) -> tuple[float | None, int]:
    """MAPE over non-zero actuals only. Returns (mape, n_excluded_zero_actuals); None if no non-zero actuals."""
    y, yhat = _arr(y), _arr(yhat)
    nz = y != 0
    excluded = int((~nz).sum())
    if not nz.any():
        return None, excluded
    return float(100 * np.mean(np.abs((y[nz] - yhat[nz]) / y[nz]))), excluded


def bias(y: object, yhat: object) -> float:
    y, yhat = _arr(y), _arr(yhat)
    return float(np.mean(yhat - y)) if y.size else math.nan


def interval_coverage(y: object, lo: object, hi: object) -> float | None:
    y, lo, hi = _arr(y), _arr(lo), _arr(hi)
    if not y.size:
        return None
    return float(np.mean((y >= lo) & (y <= hi)))


def summary(y: object, yhat: object) -> dict[str, float | int | None]:
    m, excluded = mape(y, yhat)
    return {
        "mae": _r(mae(y, yhat)),
        "rmse": _r(rmse(y, yhat)),
        "wape": _r(wape(y, yhat)),
        "mape": _r(m),
        "mape_excluded_zero_actuals": excluded,
        "bias": _r(bias(y, yhat)),
        "n": int(_arr(y).size),
    }


def _r(v: float | None, nd: int = 4) -> float | None:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return None
    return round(float(v), nd)
