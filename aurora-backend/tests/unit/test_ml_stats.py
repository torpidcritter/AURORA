from __future__ import annotations

import math

import numpy as np
import pytest

from app.ml import metrics
from app.ml.backtesting import calibration_residuals, evaluate_intervals, origins_for, rolling_origin, stat_forecasters
from app.ml.conformal import conformal_quantile, min_calibration_size, per_step_quantiles
from app.ml.statistical import croston_sba, forecast, holt_winters, is_intermittent, seasonal_naive, ses, sma


def test_metrics_zero_handling():
    assert metrics.wape([0, 0], [1, 2]) is None
    m, excl = metrics.mape([0, 10, 20], [5, 12, 18])
    assert excl == 1 and m == pytest.approx(100 * (0.2 + 0.1) / 2)
    assert metrics.mape([0, 0], [1, 1]) == (None, 2)
    assert metrics.wape([10, 10], [5, 15]) == pytest.approx(50.0)
    assert metrics.rmse([1, 2], [1, 4]) == pytest.approx(math.sqrt(2))
    assert metrics.interval_coverage([1, 5], [0, 0], [2, 2]) == 0.5


def test_sma_ses_values():
    h = np.array([1, 2, 3, 4, 5, 6, 7, 8.0])
    assert sma(h, 3).tolist() == [5.0] * 3  # mean of last 7 = (2..8)/7 = 5
    lvl = 1.0
    for y in h[1:]:
        lvl = 0.3 * y + 0.7 * lvl
    assert ses(h, 2) == pytest.approx([lvl, lvl])
    assert sma(np.array([]), 2).tolist() == [0, 0]


def test_seasonal_and_hw_capture_weekly_pattern():
    week = np.array([10, 10, 10, 10, 10, 30, 30], dtype=float)
    y = np.tile(week, 8)
    assert seasonal_naive(y, 7).tolist() == week.tolist()
    fc = holt_winters(y, 7)
    assert np.allclose(fc, week, atol=1.0)
    # short series falls back without crashing
    assert len(holt_winters(y[:10], 5)) == 5


def test_croston_and_intermittency():
    y = np.array([0, 0, 4, 0, 0, 0, 4, 0, 0, 4], dtype=float)
    assert is_intermittent(y)
    fc = croston_sba(y, 3)
    assert 0 < fc[0] < 4
    assert croston_sba(np.zeros(5), 2).tolist() == [0, 0]


def test_forecast_is_non_negative():
    y = np.array([50, 40, 30, 20, 10, 5, 1, 0, 0, 0] * 4, dtype=float)
    for m in ("sma", "ses", "holt_winters", "seasonal_naive", "croston_sba"):
        assert (forecast(m, y, 14) >= 0).all()


def test_rolling_origin_has_no_lookahead():
    calls = []

    def spy(hist, h):
        calls.append(len(hist))
        return np.zeros(h)

    y = np.arange(60, dtype=float)
    res = rolling_origin(y, 7, 4, {"spy": spy})
    assert calls == origins_for(60, 7, 4) == [32, 39, 46, 53]
    assert res["origins"] == 4
    # forecasting zeros: MAE equals mean of the actual test blocks
    expected = np.mean(np.concatenate([y[o : o + 7] for o in calls]))
    assert res["methods"]["spy"]["mae"] == pytest.approx(expected, rel=1e-3)
    assert rolling_origin(np.ones(10), 7, 4, {"spy": spy}).get("error")


def test_backtest_ranks_seasonal_method_first_on_seasonal_data():
    rng = np.random.default_rng(0)
    y = np.tile([10, 10, 10, 10, 10, 40, 40], 12).astype(float) + rng.normal(0, 1, 84)
    res = rolling_origin(y, 7, 4, stat_forecasters(["sma", "ses", "seasonal_naive", "holt_winters"]))
    assert res["best_method"] in ("seasonal_naive", "holt_winters")


def test_conformal_quantile_finite_sample():
    r = np.arange(1, 20, dtype=float)  # n=19
    assert conformal_quantile(r, 0.9) == 18.0  # ceil(20*0.9)=18th smallest
    assert conformal_quantile(r[:5], 0.9) is None
    assert min_calibration_size(0.9) == 9 and min_calibration_size(0.95) == 19
    qs = per_step_quantiles(np.array([[1, 5], [2, 1], [3, 2]] * 5, dtype=float), 0.8)
    assert qs[0] <= qs[1]


def test_conformal_coverage_on_iid_noise_is_close_to_nominal():
    rng = np.random.default_rng(42)
    y = 50 + rng.normal(0, 5, 400)
    res = calibration_residuals(y, lambda h, n: np.full(n, 50.0), 7, n_origins=300)
    ev = evaluate_intervals(res, 0.9)
    assert 0.84 <= ev["empirical_coverage"] <= 0.96
