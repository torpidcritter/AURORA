from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ImportCredential(BaseModel):
    """File imports: an optional Authorization header value. WooCommerce: a read-only consumer key and secret."""

    authorization: str | None = Field(None, max_length=2000)
    consumer_key: str | None = Field(None, max_length=200)
    consumer_secret: str | None = Field(None, max_length=200)


class ImportOptions(BaseModel):
    missing_policy: Literal["zero", "interpolate"] | None = None
    lookback_days: int | None = Field(None, ge=1, le=90)

    def for_kind(self, kind: str) -> dict:
        if kind == "woocommerce":
            return {} if self.lookback_days is None else {"lookback_days": self.lookback_days}
        return {} if self.missing_policy is None else {"missing_policy": self.missing_policy}


class ImportSourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["file_url", "woocommerce"]
    url: str = Field(min_length=1, max_length=2000)
    credential: ImportCredential | None = None
    options: ImportOptions | None = None
    frequency: Literal["hourly", "daily"] = "daily"
    hour_utc: int = Field(5, ge=0, le=23)


class ImportSourceUpdate(BaseModel):
    expected_version: int
    name: str | None = Field(None, min_length=1, max_length=200)
    url: str | None = Field(None, min_length=1, max_length=2000)
    credential: ImportCredential | None = None
    options: ImportOptions | None = None
    frequency: Literal["hourly", "daily"] | None = None
    hour_utc: int | None = Field(None, ge=0, le=23)
    is_active: bool | None = None
