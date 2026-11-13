"""Encoder-only Transformer for multi-SKU daily demand forecasting.

Input per time step (4 continuous channels):
    0 sales      - per-product standard-scaled units (scaler fit on the training segment only)
    1 dow        - day of week / 6
    2 month      - (month - 1) / 11
    3 weekend    - 1.0 on Saturday/Sunday
plus a learned product embedding (categorical SKU identity), concatenated to every time step.
SKU identifiers are *not* scaled as numbers: an arbitrary integer ID carries no ordinal meaning.

Architecture (spec section 2): Linear(4+emb -> 64) -> sinusoidal PE -> 3 x TransformerEncoderLayer(64, 4 heads,
dropout 0.1) -> last token -> Linear(64,32) -> ReLU -> Dropout(0.1) -> Linear(32, horizon).
"""

from __future__ import annotations

import math

import torch
from torch import nn

N_CONT_FEATURES = 4
FEATURE_NAMES = ["sales_history", "day_of_week", "month", "weekend"]


class PositionalEncoding(nn.Module):
    def __init__(self, d_model: int, max_len: int = 512):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        pos = torch.arange(0, max_len, dtype=torch.float32).unsqueeze(1)
        div = torch.exp(torch.arange(0, d_model, 2, dtype=torch.float32) * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(pos * div)
        pe[:, 1::2] = torch.cos(pos * div)
        self.register_buffer("pe", pe.unsqueeze(0), persistent=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.pe[:, : x.size(1)]


class TimeSeriesTransformer(nn.Module):
    def __init__(
        self,
        n_products: int,
        *,
        window: int = 30,
        horizon: int = 7,
        d_model: int = 64,
        nhead: int = 4,
        num_layers: int = 3,
        dropout: float = 0.1,
        emb_dim: int = 8,
        ff_dim: int = 128,
    ):
        super().__init__()
        if d_model % nhead:
            raise ValueError("d_model must be divisible by nhead")
        self.window, self.horizon = window, horizon
        # index 0 is reserved for "unknown product" so inference on an unseen SKU is well defined
        self.product_emb = nn.Embedding(n_products + 1, emb_dim)
        self.input_proj = nn.Linear(N_CONT_FEATURES + emb_dim, d_model)
        self.pos = PositionalEncoding(d_model, max_len=max(512, window))
        layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=ff_dim, dropout=dropout, batch_first=True
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=num_layers, enable_nested_tensor=False)
        self.head = nn.Sequential(nn.Linear(d_model, 32), nn.ReLU(), nn.Dropout(dropout), nn.Linear(32, horizon))

    def forward(self, x: torch.Tensor, product_idx: torch.Tensor) -> torch.Tensor:
        if x.dim() != 3 or x.size(-1) != N_CONT_FEATURES:
            raise ValueError(f"expected input [batch, seq, {N_CONT_FEATURES}], got {tuple(x.shape)}")
        if x.size(1) != self.window:
            raise ValueError(f"expected sequence length {self.window}, got {x.size(1)}")
        if product_idx.shape != (x.size(0),):
            raise ValueError("product_idx must have shape [batch]")
        emb = self.product_emb(product_idx).unsqueeze(1).expand(-1, x.size(1), -1)
        z = self.pos(self.input_proj(torch.cat([x, emb], dim=-1)))
        z = self.encoder(z)
        return self.head(z[:, -1, :])


def enable_mc_dropout(model: nn.Module) -> None:
    """Put the model in eval mode but re-enable *only* nn.Dropout modules.

    BatchNorm-style layers (none today) stay frozen. PyTorch's fused "fast path" for
    TransformerEncoderLayer skips dropout entirely in eval mode, so it must be disabled while sampling;
    callers use `mc_dropout_context` which does both and restores the previous state.
    """
    model.eval()
    for m in model.modules():
        if isinstance(m, nn.Dropout):
            m.train()


class mc_dropout_context:  # noqa: N801 - used as a context manager
    def __init__(self, model: nn.Module):
        self.model = model
        self._prev_fastpath: bool | None = None

    def __enter__(self) -> nn.Module:
        self._prev_fastpath = torch.backends.mha.get_fastpath_enabled()
        torch.backends.mha.set_fastpath_enabled(False)
        enable_mc_dropout(self.model)
        return self.model

    def __exit__(self, *exc: object) -> None:
        self.model.eval()
        torch.backends.mha.set_fastpath_enabled(bool(self._prev_fastpath))
