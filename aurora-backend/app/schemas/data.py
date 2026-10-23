from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class DatasetOut(ORMModel):
    id: uuid.UUID
    filename: str
    file_format: str
    size_bytes: int
    status: str
    tags: dict
    missing_policy: str
    row_count: int
    accepted_rows: int
    rejected_rows: int
    quality_score: float
    quality_report: dict
    column_mapping: dict
    created_at: datetime


class UploadResponse(BaseModel):
    dataset: DatasetOut
    duplicate: bool


class ProductOut(BaseModel):
    id: uuid.UUID
    sku: str
    name: str
    category: str | None
    unit_cost: float | None
    on_hand: int
    on_order: int
    inventory_known: bool
    lead_time_days: int | None
    service_level: float | None
    pack_size: int
    min_order_qty: int
    missing_policy: str
    version: int
    first_date: date | None = None
    last_date: date | None = None
    history_days: int = 0
    units_30d: float = 0.0
    avg_daily_30d: float = 0.0


class InventoryUpdate(BaseModel):
    expected_version: int = Field(ge=1, description="Optimistic-concurrency guard; get it from the product")
    on_hand: int | None = Field(default=None, ge=0, le=10_000_000)
    on_order: int | None = Field(default=None, ge=0, le=10_000_000)
    lead_time_days: int | None = Field(default=None, ge=1, le=365)
    service_level: float | None = Field(default=None, gt=0.5, lt=1.0)
    unit_cost: float | None = Field(default=None, ge=0, le=1e7)
    pack_size: int | None = Field(default=None, ge=1, le=100_000)
    min_order_qty: int | None = Field(default=None, ge=0, le=10_000_000)
    missing_policy: Literal["zero", "interpolate"] | None = None


class HistoryPoint(BaseModel):
    date: date
    units: float
    imputed: bool


class HistoryOut(BaseModel):
    product_id: uuid.UUID
    missing_policy: str
    points: list[HistoryPoint]
