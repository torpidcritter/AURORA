"""Audit log search and export (ADR-039). Exports are CSV or JSON Lines, capped, and themselves audited."""

from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.db.models import AuditEvent

MAX_EXPORT_ROWS = 50_000
COLUMNS = (
    "id",
    "created_at",
    "action",
    "actor_email",
    "resource_type",
    "resource_id",
    "outcome",
    "ip",
    "request_id",
    "details",
)


@dataclass(frozen=True)
class AuditFilter:
    action: str | None = None
    actor: str | None = None
    outcome: str | None = None
    since: datetime | None = None
    until: datetime | None = None

    def as_dict(self) -> dict:
        return {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in self.__dict__.items()
            if v not in (None, "")
        }


def query(org_id: uuid.UUID, f: AuditFilter) -> Select:
    q = select(AuditEvent).where(AuditEvent.org_id == org_id)
    if f.action:
        q = q.where(AuditEvent.action.startswith(f.action, autoescape=True))
    if f.actor:
        q = q.where(AuditEvent.actor_email == f.actor.lower())
    if f.outcome:
        q = q.where(AuditEvent.outcome == f.outcome)
    if f.since:
        q = q.where(AuditEvent.created_at >= f.since)
    if f.until:
        q = q.where(AuditEvent.created_at < f.until)
    return q


def count(db: Session, org_id: uuid.UUID, f: AuditFilter) -> int:
    return db.scalar(select(func.count()).select_from(query(org_id, f).subquery())) or 0


def event_out(e: AuditEvent) -> dict:
    return {
        "id": e.id,
        "created_at": e.created_at.isoformat(),
        "action": e.action,
        "actor_email": e.actor_email,
        "resource_type": e.resource_type,
        "resource_id": e.resource_id,
        "outcome": e.outcome,
        "ip": e.ip,
        "request_id": e.request_id,
        "details": e.details,
    }


def _safe_cell(v: object) -> str:
    """Neutralise spreadsheet formulas (CSV injection): actor e-mails and details are user-influenced."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def export(db: Session, org_id: uuid.UUID, f: AuditFilter, fmt: str) -> tuple[str, int]:
    """The matching events oldest first, as CSV or JSON Lines, and how many there were."""
    if f.since and f.until and f.until <= f.since:
        raise ValidationFailed("The end of the range must be after its start")
    n = count(db, org_id, f)
    if n > MAX_EXPORT_ROWS:
        raise ValidationFailed(
            f"{n:,} events match; an export holds at most {MAX_EXPORT_ROWS:,}. Narrow the date range or filters."
        )
    rows = db.scalars(query(org_id, f).order_by(AuditEvent.created_at, AuditEvent.id)).all()
    buf = io.StringIO()
    if fmt == "jsonl":
        for e in rows:
            buf.write(json.dumps(event_out(e), separators=(",", ":"), sort_keys=True))
            buf.write("\n")
    else:
        w = csv.writer(buf)
        w.writerow(COLUMNS)
        for e in rows:
            o = event_out(e)
            o["details"] = json.dumps(o["details"], separators=(",", ":"), sort_keys=True)
            w.writerow([o["id"], o["created_at"]] + [_safe_cell(o[c]) for c in COLUMNS[2:]])
    return buf.getvalue(), len(rows)
