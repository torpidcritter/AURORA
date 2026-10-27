"""Per-organisation settings, stored as overrides in ``Organization.settings``.

Every field is optional: a missing (or null) value means the global default from app/core/config.py applies. Bounds are
explicit and unknown keys are rejected. Types are strict (no "7" for 7, no true for 1), so a typo in an API client fails
loudly instead of being coerced. The title, description and ``group``/``unit`` extras are served to the settings page by
GET /settings/org, so labels and bounds live in one place.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict
from pydantic.config import JsonDict

Severity = Literal["low", "medium", "high", "critical"]
LiveMetric = Literal["mape", "wape"]
StrictInt = Annotated[int, Strict()]
StrictFloat = Annotated[float, Strict()]


def _extra(group: str, unit: str | None = None) -> JsonDict:
    out: JsonDict = {"group": group}
    if unit:
        out["unit"] = unit
    return out


class OrgSettings(BaseModel):
    """Overrides of the global defaults for one organisation. Also the PATCH body: null clears an override."""

    model_config = ConfigDict(extra="forbid")

    default_lead_time_days: StrictInt | None = Field(
        None,
        ge=1,
        le=365,
        title="Default supplier lead time",
        description="Used for products that have no lead time of their own.",
        json_schema_extra=_extra("inventory", "days"),
    )
    default_coverage_days: StrictInt | None = Field(
        None,
        ge=1,
        le=365,
        title="Default coverage window",
        description="Days of demand an order should cover after it arrives, unless a request sets its own.",
        json_schema_extra=_extra("inventory", "days"),
    )
    # The inventory math needs a service level strictly above 0.5 (z = 0 at 0.5), like the product setting.
    default_service_level: StrictFloat | None = Field(
        None,
        gt=0.5,
        le=0.999,
        allow_inf_nan=False,
        title="Default cycle service level",
        description="Probability of no stockout per replenishment cycle, for products without their own value "
        "(0.95 = 95%).",
        json_schema_extra=_extra("inventory"),
    )
    drift_ks_threshold: StrictFloat | None = Field(
        None,
        ge=0.01,
        le=1,
        allow_inf_nan=False,
        title="Drift: KS statistic threshold",
        description="Demand drift is flagged when the two-sample KS statistic (last 30 days vs before) exceeds this.",
        json_schema_extra=_extra("monitoring"),
    )
    drift_mean_change_threshold: StrictFloat | None = Field(
        None,
        ge=0.01,
        le=5,
        allow_inf_nan=False,
        title="Drift: relative mean change threshold",
        description="Demand drift is flagged when mean demand moves by more than this fraction (0.25 = 25%).",
        json_schema_extra=_extra("monitoring"),
    )
    model_max_age_days: StrictInt | None = Field(
        None,
        ge=1,
        le=365,
        title="Maximum model age",
        description="Retraining is recommended once the active model's checkpoint is older than this.",
        json_schema_extra=_extra("monitoring", "days"),
    )
    accuracy_live_metric: LiveMetric | None = Field(
        None,
        title="Live accuracy metric",
        description="Error measure for served forecasts against later sales. MAPE skips zero-demand days and is "
        "volatile for low-volume products; WAPE is steadier.",
        json_schema_extra=_extra("monitoring"),
    )
    accuracy_live_threshold: StrictFloat | None = Field(
        None,
        ge=1,
        le=500,
        allow_inf_nan=False,
        title="Live accuracy alert threshold",
        description="An accuracy alert is raised when the live metric exceeds this percentage.",
        json_schema_extra=_extra("monitoring", "%"),
    )
    alert_min_severity: Severity | None = Field(
        None,
        title="Minimum severity to notify",
        description="Open alerts at or above this severity are sent to the alert channels (log, and the webhook when "
        "one is configured). All alerts stay visible in the app.",
        json_schema_extra=_extra("alerts"),
    )
    audit_retention_days: StrictInt | None = Field(
        None,
        ge=30,
        le=3650,
        title="Audit log retention",
        description="Audit events older than this are deleted by the nightly clean-up. Export the log first if you "
        "need a longer record; lowering the value removes older events at the next run.",
        json_schema_extra=_extra("data", "days"),
    )
