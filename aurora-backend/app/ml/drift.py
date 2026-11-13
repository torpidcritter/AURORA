"""Distribution drift: two-sample Kolmogorov-Smirnov test + relative mean change."""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import ks_2samp


def detect(
    values: np.ndarray,
    recent_days: int = 30,
    baseline_days: int = 180,
    ks_threshold: float = 0.15,
    mean_threshold: float = 0.25,
    min_baseline: int = 28,
) -> dict:
    v = np.asarray(values, dtype=float)
    recent = v[-recent_days:]
    baseline = v[:-recent_days][-baseline_days:]
    if len(recent) < 7 or len(baseline) < min_baseline:
        return {
            "status": "insufficient_data",
            "drift": False,
            "recent_n": int(len(recent)),
            "baseline_n": int(len(baseline)),
            "reason": f"Need at least 7 recent and {min_baseline} baseline days",
        }
    ks = ks_2samp(recent, baseline)
    m_recent, m_base = float(recent.mean()), float(baseline.mean())
    if m_base == 0:
        # Undefined ratio: any demand after an all-zero baseline is treated as a full shift.
        ratio = math.inf if m_recent > 0 else 0.0
    else:
        ratio = abs(m_recent - m_base) / abs(m_base)
    reasons = []
    if ks.statistic > ks_threshold:
        reasons.append(f"KS statistic {ks.statistic:.3f} > {ks_threshold}")
    if ratio > mean_threshold:
        reasons.append(
            "mean shifted from zero" if math.isinf(ratio) else f"mean changed {ratio:.0%} (> {mean_threshold:.0%})"
        )
    return {
        "status": "ok",
        "drift": bool(reasons),
        "ks_statistic": round(float(ks.statistic), 4),
        "ks_pvalue": round(float(ks.pvalue), 6),
        "mean_recent": round(m_recent, 4),
        "mean_baseline": round(m_base, 4),
        "mean_change_ratio": None if math.isinf(ratio) else round(ratio, 4),
        "recent_n": int(len(recent)),
        "baseline_n": int(len(baseline)),
        "reasons": reasons,
        "thresholds": {"ks": ks_threshold, "mean_change": mean_threshold},
    }
