from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

MethodName = Literal["auto", "transformer", "sma", "ses", "holt_winters", "seasonal_naive", "croston_sba"]


class ForecastIn(BaseModel):
    product_id: uuid.UUID
    horizon: int = Field(7, ge=1, le=90)
    method: MethodName = "auto"
    coverage: Literal[0.8, 0.9, 0.95] = 0.9
    mc_samples: int = Field(20, ge=0, le=100)


class InventoryIn(BaseModel):
    product_id: uuid.UUID
    lead_time_days: float | None = Field(None, gt=0, le=365)
    lead_time_std_days: float | None = Field(None, ge=0, le=180)
    coverage_days: float | None = Field(None, gt=0, le=365)
    service_level: float | None = Field(None, gt=0.5, lt=0.9999)
    on_hand: float | None = Field(None, ge=0, le=1e9)
    on_order: float | None = Field(None, ge=0, le=1e9)


class RiskBatchIn(BaseModel):
    product_ids: list[uuid.UUID] | None = Field(None, max_length=500)


class BacktestIn(BaseModel):
    product_id: uuid.UUID
    horizon: int = Field(7, ge=1, le=56)


class Adjustment(BaseModel):
    type: str = Field(max_length=40)
    params: dict[str, float] = Field(default_factory=dict)


class ScenarioIn(BaseModel):
    product_id: uuid.UUID
    adjustments: list[Adjustment] = Field(min_length=1, max_length=5)
    horizon: int = Field(28, ge=7, le=90)


class SignalIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    signal_type: Literal["holiday", "promotion", "weather", "festival", "event", "other"]
    start_date: date
    end_date: date
    magnitude: float = Field(ge=-1.0, le=1.0)
    sku: str | None = Field(None, max_length=120)
