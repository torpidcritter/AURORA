"""Statistical forecasting baselines.

All functions take a 1-D history array (oldest first, regular daily spacing, no NaNs) and a horizon,
and return a non-negative point forecast of length ``horizon``.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable

import numpy as np

SEASON = 7
HW_MIN_OBS = 4 * SEASON  # at least four full weeks so trend and seasonality are both identifiable


def sma(history: np.ndarray, horizon: int, window: int = 7) -> np.ndarray:
    if len(history) == 0:
        return np.zeros(horizon)
    w = min(window, len(history))
    return np.full(horizon, float(np.mean(history[-w:])))


def ses(history: np.ndarray, horizon: int, alpha: float = 0.3) -> np.ndarray:
    """Simple exponential smoothing: level_t = alpha*y_t + (1-alpha)*level_{t-1}; flat forecast."""
    if len(history) == 0:
        return np.zeros(horizon)
    level = float(history[0])
    for y in history[1:]:
        level = alpha * float(y) + (1 - alpha) * level
    return np.full(horizon, max(level, 0.0))


def seasonal_naive(history: np.ndarray, horizon: int) -> np.ndarray:
    if len(history) < SEASON:
        return sma(history, horizon)
    last = history[-SEASON:]
    return np.array([last[i % SEASON] for i in range(horizon)], dtype=float)


def croston_sba(history: np.ndarray, horizon: int, alpha: float = 0.1) -> np.ndarray:
    """Croston's method with the Syntetos-Boylan bias correction, for intermittent demand."""
    nz = np.flatnonzero(history > 0)
    if len(nz) == 0:
        return np.zeros(horizon)
    z = float(history[nz[0]])
    p = float(nz[0] + 1)
    q = 1
    for y in history[nz[0] + 1 :]:
        if y > 0:
            z = alpha * float(y) + (1 - alpha) * z
            p = alpha * q + (1 - alpha) * p
            q = 1
        else:
            q += 1
    return np.full(horizon, (1 - alpha / 2) * z / max(p, 1.0))


def holt_winters(history: np.ndarray, horizon: int) -> np.ndarray:
    """Additive damped trend + additive weekly seasonality (statsmodels). Falls back to seasonal naive
    when there is too little history or the optimiser fails."""
    if len(history) < HW_MIN_OBS:
        return seasonal_naive(history, horizon)
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = ExponentialSmoothing(
                np.asarray(history, dtype=float),
                trend="add",
                damped_trend=True,
                seasonal="add",
                seasonal_periods=SEASON,
                initialization_method="estimated",
            ).fit(optimized=True)
            fc = np.asarray(model.forecast(horizon), dtype=float)
    except Exception:  # noqa: BLE001 - numerical failures in the optimiser
        return seasonal_naive(history, horizon)
    if not np.all(np.isfinite(fc)):
        return seasonal_naive(history, horizon)
    return fc


METHODS: dict[str, Callable[[np.ndarray, int], np.ndarray]] = {
    "sma": sma,
    "ses": ses,
    "holt_winters": holt_winters,
    "seasonal_naive": seasonal_naive,
    "croston_sba": croston_sba,
}

METHOD_INFO = {
    "transformer": "Encoder-only Transformer (30-day window, d_model=64, 4 heads, 3 layers) with product embeddings",
    "sma": "Simple moving average over the last 7 days",
    "ses": "Simple exponential smoothing, alpha=0.3",
    "holt_winters": "Holt-Winters: additive damped trend + additive weekly seasonality (needs >= 28 days)",
    "seasonal_naive": "Repeats the last observed week",
    "croston_sba": "Croston with Syntetos-Boylan correction, for intermittent demand",
}


def forecast(method: str, history: np.ndarray, horizon: int) -> np.ndarray:
    fc = METHODS[method](np.asarray(history, dtype=float), horizon)
    return np.clip(np.nan_to_num(fc, nan=0.0, posinf=0.0), 0.0, None)


def is_intermittent(history: np.ndarray, threshold: float = 0.3) -> bool:
    """Share of zero days above the threshold (ADI-style rule of thumb)."""
    return len(history) > 0 and float(np.mean(np.asarray(history) == 0)) > threshold
