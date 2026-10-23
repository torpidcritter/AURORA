"""Leakage-safe dataset construction, training, evaluation and (de)serialisation for the Transformer.

Per product the daily series is split chronologically into three segments:

    | train ...................... | early-stop (ES) | calibration (CAL) |

* Scalers are fit on the train segment only.
* Training windows have targets entirely inside train.
* Early-stopping windows have targets inside ES; calibration windows have targets inside CAL.
* Inputs may reach back into earlier segments (that history is observed at forecast time), but no target
  is ever used for both fitting and evaluation.
CAL residuals provide the conformal intervals and the honest Transformer-vs-baseline comparison.
"""

from __future__ import annotations

import hashlib
import io
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import torch
from torch import nn

from app.ml import metrics
from app.ml.backtesting import evaluate_intervals
from app.ml.conformal import per_step_quantiles
from app.ml.statistical import forecast as stat_forecast
from app.ml.transformer_model import FEATURE_NAMES, TimeSeriesTransformer, mc_dropout_context

FORMAT_VERSION = 1
COVERAGES = (0.8, 0.9, 0.95)
BASELINES = ("sma", "ses", "seasonal_naive", "holt_winters", "croston_sba")


@dataclass
class TrainConfig:
    window: int = 30
    horizon: int = 7
    d_model: int = 64
    nhead: int = 4
    num_layers: int = 3
    dropout: float = 0.1
    emb_dim: int = 8
    ff_dim: int = 128
    es_days: int = 14
    cal_days: int = 35
    min_train_windows: int = 14
    max_epochs: int = 30
    patience: int = 5
    batch_size: int = 64
    lr: float = 1e-3
    weight_decay: float = 1e-4
    seed: int = 1234
    time_limit_s: float = 1800.0
    threads: int = 2

    def model_kwargs(self) -> dict[str, int | float]:
        return {
            "window": self.window,
            "horizon": self.horizon,
            "d_model": self.d_model,
            "nhead": self.nhead,
            "num_layers": self.num_layers,
            "dropout": self.dropout,
            "emb_dim": self.emb_dim,
            "ff_dim": self.ff_dim,
        }

    def min_length(self) -> int:
        return self.window + self.horizon + self.min_train_windows - 1 + self.es_days + self.cal_days


def calendar_features(dates: pd.DatetimeIndex) -> np.ndarray:
    dow = dates.dayofweek.values.astype(np.float32)
    return np.stack(
        [dow / 6.0, (dates.month.values.astype(np.float32) - 1) / 11.0, (dow >= 5).astype(np.float32)], axis=1
    )


@dataclass
class Scaler:
    mean: float
    std: float

    @classmethod
    def fit(cls, values: np.ndarray) -> Scaler:
        mean = float(np.mean(values))
        std = float(np.std(values))
        return cls(mean, std if std > 1e-6 else 1.0)  # constant series: identity-scale around the mean

    def transform(self, v: np.ndarray) -> np.ndarray:
        return (v - self.mean) / self.std

    def inverse(self, v: np.ndarray) -> np.ndarray:
        return v * self.std + self.mean


def make_inputs(values: np.ndarray, dates: pd.DatetimeIndex, scaler: Scaler) -> np.ndarray:
    """[T, 4] feature matrix for a contiguous stretch of history."""
    return np.concatenate(
        [scaler.transform(values.astype(np.float32))[:, None], calendar_features(dates)], axis=1
    ).astype(np.float32)


@dataclass
class ProductSplit:
    sku: str
    index: int
    values: np.ndarray
    dates: pd.DatetimeIndex
    scaler: Scaler
    train_end: int  # exclusive index where ES segment starts
    es_end: int  # exclusive index where CAL segment starts


@dataclass
class Windows:
    x: np.ndarray  # [N, W, 4]
    y: np.ndarray  # [N, H] scaled targets
    pid: np.ndarray  # [N]
    origin: np.ndarray  # [N] index in the product's series of the first target day
    sku: list[str] = field(default_factory=list)


def split_products(series: dict[str, pd.Series], cfg: TrainConfig) -> tuple[list[ProductSplit], dict[str, str]]:
    splits, skipped = [], {}
    for sku, s in sorted(series.items()):
        s = s.astype(float)
        if s.isna().any():
            raise ValueError(f"series for {sku} contains NaN; fill gaps before training")
        if len(s) < cfg.min_length():
            skipped[sku] = f"needs {cfg.min_length()} days of history, has {len(s)}"
            continue
        n = len(s)
        es_end = n - cfg.cal_days
        train_end = es_end - cfg.es_days
        v = s.values.astype(np.float64)
        splits.append(
            ProductSplit(
                sku, len(splits) + 1, v, pd.DatetimeIndex(s.index), Scaler.fit(v[:train_end]), train_end, es_end
            )
        )
    return splits, skipped


def windows_for(ps: ProductSplit, cfg: TrainConfig, seg: str) -> Windows:
    W, H = cfg.window, cfg.horizon
    feats = make_inputs(ps.values, ps.dates, ps.scaler)
    target_scaled = ps.scaler.transform(ps.values).astype(np.float32)
    if seg == "train":
        lo, hi = W, ps.train_end - H  # first target index range [lo, hi]
    elif seg == "es":
        lo, hi = ps.train_end, ps.es_end - H
    else:
        lo, hi = ps.es_end, len(ps.values) - H
    origins = np.arange(max(lo, W), hi + 1)
    if len(origins) == 0:
        return Windows(
            np.zeros((0, W, 4), np.float32), np.zeros((0, H), np.float32), np.zeros(0, np.int64), np.zeros(0, np.int64)
        )
    x = np.stack([feats[o - W : o] for o in origins])
    y = np.stack([target_scaled[o : o + H] for o in origins])
    return Windows(x, y, np.full(len(origins), ps.index, np.int64), origins, [ps.sku] * len(origins))


def concat(ws: list[Windows]) -> Windows:
    ws = [w for w in ws if len(w.x)]
    return Windows(
        np.concatenate([w.x for w in ws]),
        np.concatenate([w.y for w in ws]),
        np.concatenate([w.pid for w in ws]),
        np.concatenate([w.origin for w in ws]),
        sum((w.sku for w in ws), []),
    )


ProgressFn = Callable[[int, int, float, float], None]
CancelFn = Callable[[], bool]


class TrainingCancelled(Exception):
    pass


def _predict(model: nn.Module, x: np.ndarray, pid: np.ndarray, batch: int = 512) -> np.ndarray:
    model.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(x), batch):
            out.append(model(torch.from_numpy(x[i : i + batch]), torch.from_numpy(pid[i : i + batch])).numpy())
    return np.concatenate(out) if out else np.zeros((0, model.horizon))  # type: ignore[arg-type]


def train(
    series: dict[str, pd.Series],
    cfg: TrainConfig,
    progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> dict:
    """Train, evaluate against baselines on the CAL segment, and return a serialisable bundle."""
    torch.manual_seed(cfg.seed)
    np.random.seed(cfg.seed)
    torch.set_num_threads(cfg.threads)
    splits, skipped = split_products(series, cfg)
    if not splits:
        raise ValueError(
            f"No product has enough history to train (minimum {cfg.min_length()} days). "
            "Use statistical methods instead."
        )
    tr = concat([windows_for(p, cfg, "train") for p in splits])
    es = concat([windows_for(p, cfg, "es") for p in splits])
    cal = concat([windows_for(p, cfg, "cal") for p in splits])

    model = TimeSeriesTransformer(len(splits), **cfg.model_kwargs())  # type: ignore[arg-type]
    opt = torch.optim.AdamW(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    loss_fn = nn.MSELoss()
    gen = torch.Generator().manual_seed(cfg.seed)
    xt, yt, pt = torch.from_numpy(tr.x), torch.from_numpy(tr.y), torch.from_numpy(tr.pid)
    best_state, best_val, bad_epochs, history = None, math.inf, 0, []
    started = time.monotonic()
    epochs_run = 0
    for epoch in range(1, cfg.max_epochs + 1):
        if should_cancel and should_cancel():
            raise TrainingCancelled()
        model.train()
        perm = torch.randperm(len(xt), generator=gen)  # shuffling *windows* is fine; split is temporal
        total = 0.0
        for i in range(0, len(perm), cfg.batch_size):
            idx = perm[i : i + cfg.batch_size]
            opt.zero_grad()
            loss = loss_fn(model(xt[idx], pt[idx]), yt[idx])
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += loss.item() * len(idx)
        train_loss = total / len(perm)
        val_loss = float(np.mean((_predict(model, es.x, es.pid) - es.y) ** 2)) if len(es.x) else train_loss
        history.append({"epoch": epoch, "train_loss": round(train_loss, 5), "val_loss": round(val_loss, 5)})
        epochs_run = epoch
        if progress:
            progress(epoch, cfg.max_epochs, train_loss, val_loss)
        if val_loss < best_val - 1e-5:
            best_val, bad_epochs = val_loss, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad_epochs += 1
            if bad_epochs >= cfg.patience:
                break
        if time.monotonic() - started > cfg.time_limit_s:
            break
    if best_state is not None:
        model.load_state_dict(best_state)

    evaluation = evaluate(model, splits, cal, cfg)
    importance = permutation_importance(model, splits, cal, repeats=5, seed=cfg.seed)
    bundle = {
        "format_version": FORMAT_VERSION,
        "state_dict": model.state_dict(),
        "config": cfg.__dict__.copy(),
        "products": {
            p.sku: {
                "index": p.index,
                "mean": p.scaler.mean,
                "std": p.scaler.std,
                "trained_through": p.dates[p.train_end - 1].date().isoformat(),
            }
            for p in splits
        },
        "feature_schema": {
            "continuous": FEATURE_NAMES,
            "categorical": ["product_embedding"],
            "scaling": "per-product standard scaling fit on the train segment",
        },
        "conformal": evaluation.pop("conformal"),
        "metrics": evaluation,
        "importance": importance,
        "training": {
            "epochs_run": epochs_run,
            "history": history,
            "best_val_loss": round(best_val, 6),
            "n_train_windows": int(len(tr.x)),
            "n_es_windows": int(len(es.x)),
            "n_cal_windows": int(len(cal.x)),
            "skipped_products": skipped,
            "duration_s": round(time.monotonic() - started, 2),
        },
    }
    return bundle


def evaluate(model: nn.Module, splits: list[ProductSplit], cal: Windows, cfg: TrainConfig) -> dict:
    """Compare Transformer and baselines on identical CAL windows; build per-product conformal quantiles."""
    pred_scaled = _predict(model, cal.x, cal.pid)
    by_sku = {p.sku: p for p in splits}
    all_actual, all_pred = [], []
    baseline_preds: dict[str, list[np.ndarray]] = {b: [] for b in BASELINES}
    per_product, conformal, coverage_eval = {}, {}, {}
    for sku in sorted(set(cal.sku)):
        mask = np.array([s == sku for s in cal.sku])
        p = by_sku[sku]
        actual = p.scaler.inverse(cal.y[mask].astype(np.float64))
        pred = np.clip(p.scaler.inverse(pred_scaled[mask].astype(np.float64)), 0, None)
        all_actual.append(actual)
        all_pred.append(pred)
        prod_base = {}
        for b in BASELINES:
            bp = np.stack([stat_forecast(b, p.values[:o], cfg.horizon) for o in cal.origin[mask]])
            baseline_preds[b].append(bp)
            prod_base[b] = metrics.wape(actual, bp)
        best_b = min(prod_base, key=lambda k: prod_base[k] if prod_base[k] is not None else math.inf)
        t_wape = metrics.wape(actual, pred)
        per_product[sku] = {
            "transformer": metrics.summary(actual, pred),
            "baselines_wape": {k: metrics._r(v) for k, v in prod_base.items()},
            "best_baseline": best_b,
            "transformer_beats_best_baseline": (
                t_wape is not None and prod_base[best_b] is not None and t_wape < prod_base[best_b]
            ),
        }
        resid = actual - pred
        conformal[sku] = {str(c): per_step_quantiles(resid, c) for c in COVERAGES}
        coverage_eval[sku] = {str(c): evaluate_intervals(resid, c) for c in COVERAGES}
    A, P = np.concatenate(all_actual), np.concatenate(all_pred)
    overall = {"transformer": metrics.summary(A, P)}
    overall["per_horizon_mae"] = [metrics._r(metrics.mae(A[:, h], P[:, h])) for h in range(cfg.horizon)]
    for b in BASELINES:
        overall[b] = metrics.summary(A, np.concatenate(baseline_preds[b]))
    t = overall["transformer"]["wape"]
    base_wapes = {b: overall[b]["wape"] for b in BASELINES if overall[b]["wape"] is not None}
    best_b = min(base_wapes, key=base_wapes.get) if base_wapes else None  # type: ignore[arg-type]
    cov_summary = {}
    for c in COVERAGES:
        vals = [coverage_eval[s][str(c)]["empirical_coverage"] for s in coverage_eval]
        vals = [v for v in vals if v is not None]
        cov_summary[str(c)] = round(float(np.mean(vals)), 4) if vals else None
    return {
        "evaluation_segment": f"last {cfg.cal_days} days per product (never used for fitting or early stopping)",
        "overall": overall,
        "best_baseline": best_b,
        "best_baseline_wape": base_wapes.get(best_b) if best_b else None,
        "transformer_wape": t,
        "per_product": per_product,
        "interval_coverage": {
            "nominal_vs_empirical": cov_summary,
            "per_product": coverage_eval,
            "method": "split conformal on |residual| per horizon step, calibrated on first 2/3 of "
            "CAL origins and measured on the last 1/3",
        },
        "conformal": conformal,
    }


def permutation_importance(
    model: nn.Module, splits: list[ProductSplit], cal: Windows, repeats: int = 5, seed: int = 0
) -> dict:
    """Permutation importance: increase in MAE (original units) when one input group is shuffled across
    evaluation windows. This is NOT SHAP."""
    if len(cal.x) < 2:
        return {"method": "permutation_importance", "features": []}
    rng = np.random.default_rng(seed)
    by_index = {p.index: p for p in splits}
    means = np.array([by_index[i].scaler.mean for i in cal.pid])[:, None]
    stds = np.array([by_index[i].scaler.std for i in cal.pid])[:, None]
    actual = cal.y * stds + means

    def mae_of(x: np.ndarray, pid: np.ndarray) -> float:
        pred = np.clip(_predict(model, x, pid) * stds + means, 0, None)
        return float(np.mean(np.abs(actual - pred)))

    base = mae_of(cal.x, cal.pid)
    results = []
    groups = {name: i for i, name in enumerate(FEATURE_NAMES)}
    for name, ch in [*groups.items(), ("product_identity", None)]:
        deltas = []
        for _ in range(repeats):
            perm = rng.permutation(len(cal.x))
            if ch is None:
                deltas.append(mae_of(cal.x, cal.pid[perm]) - base)
            else:
                x = cal.x.copy()
                x[:, :, ch] = cal.x[perm][:, :, ch]
                deltas.append(mae_of(x, cal.pid) - base)
        results.append(
            {
                "feature": name,
                "delta_mae_mean": round(float(np.mean(deltas)), 4),
                "delta_mae_std": round(float(np.std(deltas)), 4),
            }
        )
    results.sort(key=lambda r: r["delta_mae_mean"], reverse=True)
    return {
        "method": "permutation_importance",
        "metric": "increase in MAE (units/day) when shuffled",
        "repeats": repeats,
        "baseline_mae": round(base, 4),
        "features": results,
    }


# --- persistence --------------------------------------------------------------------------------------


def serialize(bundle: dict) -> tuple[bytes, str]:
    buf = io.BytesIO()
    torch.save(bundle, buf)
    data = buf.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def deserialize(data: bytes, expected_sha256: str | None = None) -> tuple[dict, TimeSeriesTransformer]:
    if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("model artifact checksum mismatch")
    bundle = torch.load(io.BytesIO(data), map_location="cpu", weights_only=True)
    if bundle.get("format_version") != FORMAT_VERSION:
        raise ValueError(f"unsupported model format {bundle.get('format_version')}")
    cfg = TrainConfig(**bundle["config"])
    model = TimeSeriesTransformer(len(bundle["products"]), **cfg.model_kwargs())  # type: ignore[arg-type]
    model.load_state_dict(bundle["state_dict"])
    model.eval()
    return bundle, model


# --- inference ----------------------------------------------------------------------------------------


@dataclass
class TransformerForecast:
    mean: np.ndarray
    mc_mean: np.ndarray
    mc_std: np.ndarray


def predict(
    bundle: dict,
    model: TimeSeriesTransformer,
    sku: str,
    values: np.ndarray,
    dates: pd.DatetimeIndex,
    mc_samples: int = 20,
) -> TransformerForecast:
    info = bundle["products"].get(sku)
    if info is None:
        raise KeyError(f"product {sku} was not part of this model's training set")
    W = model.window
    if len(values) < W:
        raise ValueError(f"need {W} days of history, have {len(values)}")
    scaler = Scaler(info["mean"], info["std"])
    x = torch.from_numpy(make_inputs(np.asarray(values[-W:], dtype=np.float64), dates[-W:], scaler)[None])
    pid = torch.tensor([info["index"]], dtype=torch.long)
    model.eval()
    with torch.no_grad():
        point = scaler.inverse(model(x, pid).numpy()[0].astype(np.float64))
    samples = []
    if mc_samples > 0:
        with mc_dropout_context(model), torch.no_grad():
            xb = x.expand(mc_samples, -1, -1)
            samples = scaler.inverse(model(xb, pid.expand(mc_samples)).numpy().astype(np.float64))
    mc = np.asarray(samples) if len(samples) else point[None]
    return TransformerForecast(np.clip(point, 0, None), np.clip(mc.mean(axis=0), 0, None), mc.std(axis=0))
