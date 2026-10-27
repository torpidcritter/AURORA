from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import torch

from app.devtools.synthetic import generate_sales
from app.ml import training
from app.ml.training import TrainConfig, split_products, windows_for
from app.ml.transformer_model import TimeSeriesTransformer, mc_dropout_context


def synthetic_series(n=4, days=200):
    df = generate_sales(n_products=n, days=days, intermittent=False)
    out = {}
    for sku, g in df.groupby("sku"):
        out[sku] = pd.Series(g["units_sold"].astype(float).values, index=pd.to_datetime(g["date"]))
    return out


SMALL = TrainConfig(max_epochs=4, d_model=32, nhead=4, num_layers=1, patience=2)


def test_forward_shapes_and_validation():
    m = TimeSeriesTransformer(3, window=30, horizon=7)
    out = m(torch.zeros(5, 30, 4), torch.tensor([0, 1, 2, 3, 1]))
    assert out.shape == (5, 7)
    with pytest.raises(ValueError):
        m(torch.zeros(5, 29, 4), torch.zeros(5, dtype=torch.long))
    with pytest.raises(ValueError):
        m(torch.zeros(5, 30, 5), torch.zeros(5, dtype=torch.long))
    # spec architecture
    assert m.input_proj.out_features == 64
    assert len(m.encoder.layers) == 3
    assert m.encoder.layers[0].self_attn.num_heads == 4


def test_temporal_split_has_no_target_leakage():
    cfg = TrainConfig()
    splits, skipped = split_products(synthetic_series(2, 200), cfg)
    assert not skipped
    p = splits[0]
    n = len(p.values)
    assert p.es_end == n - cfg.cal_days and p.train_end == p.es_end - cfg.es_days
    # scaler only sees the train segment
    assert p.scaler.mean == pytest.approx(np.mean(p.values[: p.train_end]))
    tr, es, cal = (windows_for(p, cfg, s) for s in ("train", "es", "cal"))
    assert (tr.origin + cfg.horizon <= p.train_end).all()
    assert (es.origin >= p.train_end).all() and (es.origin + cfg.horizon <= p.es_end).all()
    assert (cal.origin >= p.es_end).all() and (cal.origin + cfg.horizon <= n).all()
    # inputs end exactly where targets begin; target values align with the raw series
    o = int(cal.origin[3])
    assert np.allclose(p.scaler.inverse(cal.y[3]), p.values[o : o + cfg.horizon], atol=1e-3)
    assert np.allclose(p.scaler.inverse(cal.x[3][:, 0]), p.values[o - cfg.window : o], atol=1e-3)
    assert len(cal.x) == cfg.cal_days - cfg.horizon + 1


def test_short_series_are_skipped():
    s = synthetic_series(2, 200)
    s["SKU-SHORT"] = s["SKU-001"].iloc[:50]
    splits, skipped = split_products(s, TrainConfig())
    assert "SKU-SHORT" in skipped and all(p.sku != "SKU-SHORT" for p in splits)


def test_nan_rejected():
    s = synthetic_series(1, 200)
    s["SKU-001"].iloc[5] = np.nan
    with pytest.raises(ValueError):
        split_products(s, TrainConfig())


@pytest.mark.slow
def test_train_save_load_predict_roundtrip():
    series = synthetic_series(3, 220)
    seen = []
    bundle = training.train(series, SMALL, progress=lambda e, m, tl, vl: seen.append(e))
    assert seen and seen[0] == 1
    m = bundle["metrics"]
    assert m["transformer_wape"] is not None
    assert set(m["overall"]) >= {"transformer", "sma", "ses", "holt_winters", "seasonal_naive"}
    assert len(bundle["conformal"]["SKU-001"]["0.9"]) == SMALL.horizon
    assert bundle["importance"]["method"] == "permutation_importance"

    data, digest = training.serialize(bundle)
    b2, model = training.deserialize(data, digest)  # simulates a process restart: only bytes survive
    s = series["SKU-002"]
    f1 = training.predict(b2, model, "SKU-002", s.values, pd.DatetimeIndex(s.index), mc_samples=0)
    b3, model3 = training.deserialize(data)
    f2 = training.predict(b3, model3, "SKU-002", s.values, pd.DatetimeIndex(s.index), mc_samples=0)
    assert np.allclose(f1.mean, f2.mean)
    assert (f1.mean >= 0).all() and f1.mean.shape == (SMALL.horizon,)
    with pytest.raises(ValueError):
        training.deserialize(data, "0" * 64)
    with pytest.raises(KeyError):
        training.predict(b2, model, "UNKNOWN", s.values, pd.DatetimeIndex(s.index))


def test_mc_dropout_is_stochastic_only_when_enabled():
    torch.manual_seed(0)
    m = TimeSeriesTransformer(2, window=30, horizon=7)
    x = torch.randn(1, 30, 4).expand(16, -1, -1)
    pid = torch.ones(16, dtype=torch.long)
    m.eval()
    with torch.no_grad():
        det = m(x, pid)
    assert torch.allclose(det, det[0].expand_as(det))  # eval mode: deterministic
    with mc_dropout_context(m), torch.no_grad():
        mc = m(x, pid)
    assert mc.std(dim=0).mean() > 0  # dropout really active (fast path disabled)
    assert not m.training and all(not mod.training for mod in m.modules())  # restored afterwards
    assert torch.backends.mha.get_fastpath_enabled()


def test_cancellation():
    with pytest.raises(training.TrainingCancelled):
        training.train(synthetic_series(2, 200), SMALL, should_cancel=lambda: True)


@pytest.mark.parametrize("horizon", [7, 14, 28, 56])
def test_every_supported_horizon_has_held_out_windows(horizon):
    cfg = TrainConfig(horizon=horizon)
    splits, skipped = split_products(synthetic_series(1, cfg.min_length() + 10), cfg)
    assert not skipped
    p = splits[0]
    tr, es, cal = (windows_for(p, cfg, seg) for seg in ("train", "es", "cal"))
    assert len(es.x) == cfg.es_windows == 8
    assert len(cal.x) == cfg.cal_windows == 29
    assert len(tr.x) >= cfg.min_train_windows
    if horizon == 7:  # unchanged from the original fixed 14/35-day segments
        assert (cfg.es_days, cfg.cal_days) == (14, 35)


def test_training_at_horizon_28_succeeds():
    cfg = TrainConfig(horizon=28, max_epochs=2, d_model=32, nhead=4, num_layers=1, patience=1)
    bundle = training.train(synthetic_series(2, cfg.min_length() + 20), cfg)
    assert bundle["config"]["horizon"] == 28
    data, digest = training.serialize(bundle)
    _, model = training.deserialize(data, digest)
    assert model.horizon == 28


def test_bundles_saved_with_day_based_segments_still_load():
    old = {**TrainConfig().__dict__, "es_days": 14, "cal_days": 35}
    old.pop("es_windows")
    old.pop("cal_windows")
    cfg = TrainConfig.from_dict(old)
    assert (cfg.es_windows, cfg.cal_windows, cfg.es_days, cfg.cal_days) == (8, 29, 14, 35)
