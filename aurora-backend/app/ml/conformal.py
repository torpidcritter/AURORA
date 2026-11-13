"""Split-conformal prediction intervals from absolute residuals, calibrated per horizon step.

With n exchangeable calibration residuals, the interval yhat +/- q where
q = the ceil((n+1)(1-alpha))-th smallest |residual| has marginal coverage >= 1-alpha
*if* future errors are exchangeable with the calibration errors. Time series errors are only
approximately exchangeable, so Aurora reports measured (empirical) coverage on held-out origins alongside
every interval rather than claiming a guarantee.
"""

from __future__ import annotations

import math

import numpy as np


def conformal_quantile(abs_residuals: np.ndarray, coverage: float) -> float | None:
    r = np.sort(np.asarray(abs_residuals, dtype=float)[np.isfinite(abs_residuals)])
    n = len(r)
    if n == 0:
        return None
    k = math.ceil(round((n + 1) * coverage, 9))  # round away float noise such as 18.000000000000004
    if k > n:
        return None  # not enough calibration points for this coverage level
    return float(r[k - 1])


def min_calibration_size(coverage: float) -> int:
    return math.ceil(round(coverage / (1 - coverage), 9))


def per_step_quantiles(residuals: np.ndarray, coverage: float) -> list[float | None]:
    """residuals: shape (n_origins, horizon). Returns one quantile per horizon step.

    If a step has too few residuals, residuals from all steps up to and including it are pooled
    (a conservative widening, since later steps usually have larger errors)."""
    res = np.abs(np.asarray(residuals, dtype=float))
    if res.ndim != 2 or res.size == 0:
        return []
    out: list[float | None] = []
    for h in range(res.shape[1]):
        q = conformal_quantile(res[:, h], coverage)
        if q is None:
            q = conformal_quantile(res[:, : h + 1].ravel(), coverage)
        out.append(q)
    # enforce non-decreasing widths across the horizon
    running = 0.0
    for i, q in enumerate(out):
        if q is not None:
            running = max(running, q)
            out[i] = running
    return out
