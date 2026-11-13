"""Request bodies for suppliers, purchase orders and forecast overrides. Unknown fields are rejected."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


class SupplierIn(_Strict):
    name: str = Field(min_length=1, max_length=200)
    contact_name: str | None = Field(None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50)
    lead_time_days: int | None = Field(None, ge=1, le=365)
    min_order_value: float | None = Field(None, ge=0, le=1e9, allow_inf_nan=False)
    notes: str | None = Field(None, max_length=2000)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("contact_name", "phone", "notes")
    @classmethod
    def _opt(cls, v: str | None) -> str | None:
        return _clean(v)


class SupplierUpdate(_Strict):
    """Partial update: fields left out are unchanged; null clears an optional field."""

    expected_version: int = Field(ge=1)
    name: str | None = Field(None, min_length=1, max_length=200)
    contact_name: str | None = Field(None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50)
    lead_time_days: int | None = Field(None, ge=1, le=365)
    min_order_value: float | None = Field(None, ge=0, le=1e9, allow_inf_nan=False)
    notes: str | None = Field(None, max_length=2000)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("is_active")
    @classmethod
    def _active(cls, v: bool | None) -> bool:
        if v is None:
            raise ValueError("is_active must be true or false")
        return v

    @field_validator("contact_name", "phone", "notes")
    @classmethod
    def _opt(cls, v: str | None) -> str | None:
        return _clean(v)


class AssignProductsIn(_Strict):
    supplier_id: uuid.UUID | None = Field(description="null removes the products' supplier")
    product_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)


class POLineIn(_Strict):
    product_id: uuid.UUID
    quantity: int = Field(ge=1, le=10_000_000)
    unit_cost: float | None = Field(None, ge=0, le=1e7, allow_inf_nan=False)


class POCreate(_Strict):
    supplier_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = Field(None, description="Deliver to; defaults to the default location")
    expected_date: date | None = None
    notes: str | None = Field(None, max_length=2000)
    lines: list[POLineIn] = Field(default_factory=list, max_length=500)


class POUpdate(_Strict):
    expected_version: int = Field(ge=1)
    supplier_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    expected_date: date | None = None
    notes: str | None = Field(None, max_length=2000)
    lines: list[POLineIn] | None = Field(None, max_length=500)


class POVersion(_Strict):
    expected_version: int = Field(ge=1, description="Guards against acting on an order someone else just changed")


class ReceiveLine(_Strict):
    product_id: uuid.UUID
    quantity: int = Field(ge=1, le=10_000_000)


class POReceive(POVersion):
    lines: list[ReceiveLine] | None = Field(
        None, max_length=500, description="Leave out to receive everything still outstanding"
    )


class SuggestIn(_Strict):
    product_ids: list[uuid.UUID] | None = Field(None, max_length=1000)
    # From a budget plan: draft exactly these quantities (for these products only) instead of the suggestions.
    quantities: dict[uuid.UUID, Annotated[int, Field(gt=0, le=10_000_000)]] | None = Field(None, max_length=1000)


class BudgetPlanIn(_Strict):
    budget: float = Field(ge=0, le=1e12)
    policy: Literal["service", "urgency"] = "service"
    category: str | None = Field(None, max_length=120)
    product_ids: list[uuid.UUID] | None = Field(None, max_length=1000)


class OverrideIn(_Strict):
    product_id: uuid.UUID
    start_date: date
    end_date: date
    kind: Literal["set", "scale"] = Field(
        description="set: demand is this many units per day; scale: the model's forecast times this factor"
    )
    value: float = Field(ge=0, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=500)


# --- locations --------------------------------------------------------------------------------------------------------

_CODE = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$"


class LocationIn(_Strict):
    code: str = Field(
        pattern=_CODE, description="Short unique code, used in inventory uploads (letters, digits, - _ .)"
    )
    name: str = Field(min_length=1, max_length=200)
    address: str | None = Field(None, max_length=1000)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("address")
    @classmethod
    def _addr(cls, v: str | None) -> str | None:
        return _clean(v)


class LocationUpdate(_Strict):
    expected_version: int = Field(ge=1)
    code: str | None = Field(None, pattern=_CODE)
    name: str | None = Field(None, min_length=1, max_length=200)
    address: str | None = Field(None, max_length=1000)
    is_active: bool | None = None
    is_default: Literal[True] | None = Field(None, description="Make this the default location (cannot be unset)")

    @field_validator("code", "name")
    @classmethod
    def _required(cls, v: str | None) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("is_active")
    @classmethod
    def _active(cls, v: bool | None) -> bool:
        if v is None:
            raise ValueError("is_active must be true or false")
        return v

    @field_validator("address")
    @classmethod
    def _addr(cls, v: str | None) -> str | None:
        return _clean(v)


class TransferIn(_Strict):
    product_id: uuid.UUID
    from_location_id: uuid.UUID
    to_location_id: uuid.UUID
    quantity: int = Field(ge=1, le=10_000_000)
    note: str | None = Field(None, max_length=500)


class PriceBreak(_Strict):
    min_qty: int = Field(gt=1, le=10_000_000)
    unit_cost: float = Field(ge=0, le=1e7)


class PriceBreaksIn(_Strict):
    expected_version: int = Field(ge=1)
    breaks: list[PriceBreak] = Field(max_length=10)
