"""Organisation settings: per-org overrides (``Organization.settings``) merged over the global defaults.

Only the keys of ``OrgSettings`` are owned here. Other keys in the same JSONB document belong to other features and are
left untouched on update and ignored on read. Stored values are re-validated on every read: an invalid value (for
example from a manual database edit) falls back to the global default instead of breaking every request for the org.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.db.models import Organization
from app.schemas.settings import OrgSettings
from app.services import alert_service

logger = logging.getLogger("aurora.settings")

KEYS: tuple[str, ...] = tuple(OrgSettings.model_fields)
Source = Literal["org", "default"]


@dataclass(frozen=True)
class EffectiveSettings:
    """The values the product uses for one organisation, and where each came from."""

    default_lead_time_days: int
    default_coverage_days: int
    default_service_level: float
    drift_ks_threshold: float
    drift_mean_change_threshold: float
    model_max_age_days: int
    accuracy_live_metric: str
    accuracy_live_threshold: float
    alert_min_severity: str
    sources: dict[str, Source]

    def is_override(self, key: str) -> bool:
        return self.sources[key] == "org"

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in KEYS}


def global_defaults() -> dict[str, Any]:
    s = get_settings()
    return {k: getattr(s, k) for k in KEYS}


def overrides_from(raw: dict[str, Any] | None) -> dict[str, Any]:
    """The valid overrides in a stored settings document, keyed in schema order."""
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for k in KEYS:
        if raw.get(k) is None:
            continue
        try:
            out[k] = getattr(OrgSettings.model_validate({k: raw[k]}), k)
        except ValidationError:
            logger.warning("ignoring invalid organisation setting", extra={"extra_fields": {"key": k}})
    return out


def resolve(raw: dict[str, Any] | None) -> EffectiveSettings:
    """Effective settings for a stored settings document (``None`` = global defaults only)."""
    defaults = global_defaults()
    overrides = overrides_from(raw)
    return EffectiveSettings(
        **{k: overrides.get(k, defaults[k]) for k in KEYS},
        sources={k: ("org" if k in overrides else "default") for k in KEYS},
    )


def _stored(db: Session, org_id: uuid.UUID) -> dict[str, Any]:
    # A column query, not db.get(): sessions keep objects across commits (expire_on_commit=False), so the identity map
    # could hold settings from before another session's update.
    row = db.execute(select(Organization.settings).where(Organization.id == org_id)).first()
    if row is None:
        raise NotFound("Organisation not found")
    return row[0] or {}


def for_org(db: Session, org_id: uuid.UUID) -> EffectiveSettings:
    return resolve(_stored(db, org_id))


@cache
def _field_meta() -> dict[str, dict[str, Any]]:
    """Label, description, type and bounds of each setting, from the schema's JSON Schema."""
    props = OrgSettings.model_json_schema()["properties"]
    out: dict[str, dict[str, Any]] = {}
    for k in KEYS:
        p = props[k]
        branch = next(b for b in p.get("anyOf", [p]) if b.get("type") != "null")
        meta: dict[str, Any] = {
            "label": p.get("title", k),
            "description": p.get("description", ""),
            "group": p.get("group", "general"),
            "unit": p.get("unit"),
            "type": "choice" if "enum" in branch else branch["type"],
            "minimum": branch.get("minimum", branch.get("exclusiveMinimum")),
            "maximum": branch.get("maximum", branch.get("exclusiveMaximum")),
            "minimum_exclusive": "exclusiveMinimum" in branch,
            "maximum_exclusive": "exclusiveMaximum" in branch,
            "choices": branch.get("enum"),
        }
        out[k] = meta
    return out


def describe(db: Session, org_id: uuid.UUID) -> dict[str, Any]:
    """Every setting with its effective value, global default, the org's override (or null) and its bounds."""
    stored = _stored(db, org_id)
    defaults = global_defaults()
    overrides = overrides_from(stored)
    meta = _field_meta()
    items = [
        {
            "key": k,
            **meta[k],
            "value": overrides.get(k, defaults[k]),
            "default": defaults[k],
            "override": overrides.get(k),
            "source": "org" if k in overrides else "default",
        }
        for k in KEYS
    ]
    return {"items": items, "alert_channels": alert_service.channels()}


def update(db: Session, org_id: uuid.UUID, changes: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply a partial update (a ``None`` value clears that override) and return the overrides (before, after).

    The organisation row is locked (SELECT ... FOR UPDATE) for the read-modify-write, so concurrent updates are
    serialised instead of one overwriting the other's keys. The caller commits, which releases the lock.
    """
    try:
        validated = OrgSettings.model_validate(changes)
    except ValidationError as exc:
        details = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        raise ValidationFailed("Invalid settings", details=details) from exc
    clean = validated.model_dump(include=set(changes))
    org = db.scalar(
        select(Organization)
        .where(Organization.id == org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if org is None:
        raise NotFound("Organisation not found")
    raw = dict(org.settings or {})
    before = overrides_from(raw)
    for k in KEYS:  # drop stored values that no longer validate (they were already ignored on read)
        if k in raw and k not in before:
            raw.pop(k)
    for k, v in clean.items():
        if v is None:
            raw.pop(k, None)
        else:
            raw[k] = v
    org.settings = raw  # a new dict, so the JSONB change is detected
    return before, overrides_from(raw)
