from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Section = Literal["stockouts", "overstock", "accuracy", "alerts", "purchasing"]
Channel = Literal["in_app", "webhook", "slack", "email"]


class DigestScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    frequency: Literal["daily", "weekly"] = "daily"
    weekday: int | None = Field(None, ge=0, le=6)
    hour_utc: int = Field(7, ge=0, le=23)
    sections: list[Section] = Field(min_length=1, max_length=5)
    channel: Channel = "in_app"
    target: str | None = Field(None, max_length=2000)


class DigestScheduleUpdate(BaseModel):
    expected_version: int
    name: str | None = Field(None, min_length=1, max_length=200)
    frequency: Literal["daily", "weekly"] | None = None
    weekday: int | None = Field(None, ge=0, le=6)
    hour_utc: int | None = Field(None, ge=0, le=23)
    sections: list[Section] | None = Field(None, min_length=1, max_length=5)
    channel: Channel | None = None
    target: str | None = Field(None, max_length=2000)
    is_active: bool | None = None


class DigestPreviewIn(BaseModel):
    sections: list[Section] = Field(min_length=1, max_length=5)
    period_days: Literal[1, 7] = 1
