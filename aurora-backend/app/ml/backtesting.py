"""Rolling-origin evaluation and residual collection for conformal calibration.

No shuffling: every forecast is produced only from data strictly before its origin.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import numpy as np

from app.ml import metrics
from app.ml.conformal import per_step_quantiles
from app.ml.statistical import forecast as stat_forecast

Forecaster = Callable[[np.ndarray, int], np.ndarray]
MIN_TRAIN = 14


def origins_for(n: int, horizon: int, folds: int, min_train: int = MIN_TRAIN) -> list[int]:
    """Non-overlapping test blocks ending at the last observation: [o, o+h) for each origin o."""
    return [o for o in (n - horizon * (folds - k) for k in range(folds)) if o >= min_train]


def rolling_origin(
    y: np.ndarray, horizon: int, folds: int, forecasters: dict[str, Forecaster], min_train: int = MIN_TRAIN
) -> dict[str, object]:
    y = np.asarray(y, dtype=float)
    origins = origins_for(len(y), horizon, folds, min_train)
    out: dict[str, object] = {"origins": len(origins), "horizon": horizon, "methods": {}}
    if not origins:
        out["error"] = f"Need at least {min_train + horizon} days of history for a backtest at horizon {horizon}"
        return out
    for name, fn in forecasters.items():
        preds, acts = [], []
        for o in origins:
            preds.append(np.clip(fn(y[:o], horizon), 0, None))
            acts.append(y[o : o + horizon])
        p, a = np.vstack(preds), np.vstack(acts)
        per_step = [metrics._r(metrics.mae(a[:, h], p[:, h])) for h in range(horizon)]
        out["methods"][name] = {  # type: ignore[index]
            **metrics.summary(a, p),
            "per_horizon_mae": per_step,
            "folds": [metrics.summary(a[i], p[i]) for i in range(len(origins))],
        }
    ranked = sorted(
        out["methods"].items(),  # type: ignore[union-attr]
        key=lambda kv: (kv[1]["wape"] if kv[1]["wape"] is not None else float("inf"), kv[1]["mae"]),
    )
    out["best_method"] = ranked[0][0]
    out["ranking"] = [k for k, _ in ranked]
    return out


def calibration_residuals(
    y: np.ndarray, fn: Forecaster, horizon: int, n_origins: int = 42, min_train: int = MIN_TRAIN, stride: int = 1
) -> np.ndarray:
    """Residuals (actual - forecast), shape (n_origins, horizon), from dense rolling origins."""
    y = np.asarray(y, dtype=float)
    last = len(y) - horizon
    origins = [o for o in range(last - (n_origins - 1) * stride, last + 1, stride) if o >= min_train]
    rows = [y[o : o + horizon] - np.clip(fn(y[:o], horizon), 0, None) for o in origins]
    return np.vstack(rows) if rows else np.zeros((0, horizon))


def evaluate_intervals(residuals: np.ndarray, coverage: float) -> dict[str, float | int | None]:
    """Honest coverage check: calibrate on the earlier 2/3 of origins, measure on the later 1/3."""
    n = residuals.shape[0]
    if n < 6:
        return {"empirical_coverage": None, "mean_width": None, "n_calibration": n, "n_test": 0}
    split = (2 * n) // 3
    qs = per_step_quantiles(residuals[:split], coverage)
    test = residuals[split:]
    if not qs or any(q is None for q in qs):
        return {"empirical_coverage": None, "mean_width": None, "n_calibration": split, "n_test": n - split}
    q = np.array(qs, dtype=float)
    inside = np.abs(test) <= q[None, :]
    return {
        "empirical_coverage": round(float(inside.mean()), 4),
        "mean_width": round(float(2 * q.mean()), 4),
        "n_calibration": split,
        "n_test": n - split,
    }


def stat_forecasters(methods: Iterable[str]) -> dict[str, Forecaster]:
    return {m: (lambda h, n, _m=m: stat_forecast(_m, h, n)) for m in methods}
