"""Data export and deletion (ADR-042).

* Organisation export: every business table as CSV in one ZIP, built by a background job and kept for 7 days.
* Personal export: the signed-in user's own profile, sessions and audit events as JSON.
* Account erasure: an administrator removes a person's account; the row is anonymised rather than deleted because the
  append-only audit log references it (ADR-024).
* Organisation deletion: an administrator schedules it with a 7-day grace period; a nightly task then deletes every row
  and stored file of the organisation.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import secrets
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.security import hash_password, verify_password
from app.db import models as m
from app.services import audit, auth_service, two_factor
from app.storage import get_storage

logger = logging.getLogger("aurora.data_rights")

EXPORT_TTL = timedelta(days=7)
DELETION_GRACE = timedelta(days=7)
DELETION_KEY = "deletion"  # in Organization.settings: {"due_at", "requested_at", "requested_by"}
# Secrets and credentials never leave the database, whoever asks.
SECRET_COLUMNS = frozenset(
    {
        "password_hash",
        "totp_secret",
        "totp_pending",
        "recovery_codes",
        "token_hash",
        "target_encrypted",
        "access_token_enc",
    }
)
EXPORT_TABLES: tuple[type, ...] = (
    m.User,
    m.Location,
    m.Supplier,
    m.Product,
    m.SalesRecord,
    m.StockLevel,
    m.StockTransfer,
    m.PurchaseOrder,
    m.PurchaseOrderLine,
    m.ForecastOverride,
    m.ExternalSignal,
    m.Alert,
    m.Dataset,
    m.ModelVersion,
    m.DigestSchedule,
    m.DigestRun,
    m.Invitation,
    m.AuditEvent,
)


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, dict | list):
        return json.dumps(v, separators=(",", ":"), sort_keys=True, default=str)
    if isinstance(v, Decimal | float | int | bool | uuid.UUID):
        return str(v)
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s  # spreadsheet formula neutralising


def _columns(model: type) -> list[str]:
    return [c.name for c in model.__table__.columns if c.name not in SECRET_COLUMNS]  # type: ignore[attr-defined]


def build_org_export(db: Session, org_id: uuid.UUID, progress=None) -> tuple[bytes, dict[str, int]]:  # type: ignore[no-untyped-def]
    """A ZIP with one CSV per table (secrets removed) and a manifest. Rows are streamed from the database."""
    counts: dict[str, int] = {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i, model in enumerate(EXPORT_TABLES):
            table = model.__table__  # type: ignore[attr-defined]
            cols = _columns(model)
            stmt = select(*[table.c[c] for c in cols]).where(table.c.org_id == org_id)
            if "created_at" in cols:
                stmt = stmt.order_by(table.c.created_at)
            n = 0
            with zf.open(f"{table.name}.csv", "w") as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                w = csv.writer(text)
                w.writerow(cols)
                for row in db.execute(stmt.execution_options(yield_per=5000)):
                    w.writerow([_cell(v) for v in row])
                    n += 1
                text.flush()
                text.detach()
            counts[table.name] = n
            if progress:
                progress((i + 1) / len(EXPORT_TABLES))
        org = db.get(m.Organization, org_id)
        manifest = {
            "organisation": {"id": str(org_id), "name": org.name if org else None},
            "created_at": datetime.now(UTC).isoformat(),
            "format": "CSV, UTF-8, one file per table; JSON columns are JSON text; secrets are not included",
            "rows": counts,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    return buf.getvalue(), counts


def export_key(org_id: uuid.UUID, job_id: uuid.UUID) -> str:
    return f"exports/{org_id}/{job_id}.zip"


def run_export_job(db: Session, job: m.Job, progress=None) -> dict:  # type: ignore[no-untyped-def]
    data, counts = build_org_export(db, job.org_id, progress)
    key = export_key(job.org_id, job.id)
    get_storage().put(key, data)
    return {
        "key": key,
        "bytes": len(data),
        "rows": counts,
        "expires_at": (datetime.now(UTC) + EXPORT_TTL).isoformat(),
    }


def export_download(db: Session, org_id: uuid.UUID, job_id: uuid.UUID) -> tuple[bytes, str]:
    job = db.scalar(select(m.Job).where(m.Job.id == job_id, m.Job.org_id == org_id, m.Job.kind == "data_export"))
    if job is None:
        raise NotFound("Export not found")
    if job.status != "succeeded" or not job.result or job.result.get("expired"):
        raise Conflict("This export is not available (still running, failed or expired)")
    data = get_storage().get(job.result["key"])
    stamp = (job.finished_at or datetime.now(UTC)).strftime("%Y%m%d")
    return data, f"aurora-export-{stamp}.zip"


def expire_exports(db: Session, now: datetime | None = None) -> int:
    """Delete export files older than EXPORT_TTL; the job row stays as a record, marked expired."""
    now = now or datetime.now(UTC)
    n = 0
    jobs = db.scalars(
        select(m.Job).where(
            m.Job.kind == "data_export", m.Job.status == "succeeded", m.Job.finished_at < now - EXPORT_TTL
        )
    ).all()
    for job in jobs:
        if not job.result or job.result.get("expired"):
            continue
        try:
            get_storage().delete(job.result["key"])
        except Exception:  # noqa: BLE001 - retried on the next run
            logger.warning("could not delete export %s", job.id, exc_info=True)
            continue
        job.result = {**job.result, "expired": True}
        n += 1
    db.commit()
    return n


# --- personal data ---------------------------------------------------------------------------------------------------


def personal_export(db: Session, user: m.User) -> dict:
    events = db.scalars(
        select(m.AuditEvent)
        .where(m.AuditEvent.actor_user_id == user.id)
        .order_by(m.AuditEvent.created_at.desc())
        .limit(10_000)
    ).all()
    sessions = db.scalars(select(m.RefreshToken).where(m.RefreshToken.user_id == user.id)).all()
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "profile": {c: _json(getattr(user, c)) for c in _columns(m.User)}
        | {"two_factor_enabled": user.two_factor_enabled},
        "sessions": [
            {
                "created_at": _json(t.created_at),
                "expires_at": _json(t.expires_at),
                "revoked_at": _json(t.revoked_at),
                "ip": t.ip,
                "user_agent": t.user_agent,
            }
            for t in sessions
        ],
        "audit_events": [
            {
                "created_at": _json(e.created_at),
                "action": e.action,
                "outcome": e.outcome,
                "ip": e.ip,
                "resource_type": e.resource_type,
                "resource_id": e.resource_id,
                "details": e.details,
            }
            for e in events
        ],
        "note": "Business records you created (orders, overrides and so on) belong to your organisation and are in its "
        "export, not here. At most the latest 10,000 audit events are listed.",
    }


def _json(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def erase_user(db: Session, admin: m.User, user_id: uuid.UUID) -> str:
    """Remove a person's account: their name and e-mail are replaced, they can never sign in again, and their
    sessions, reset links and two-factor login are removed. Returns the former e-mail for the audit record."""
    if user_id == admin.id:
        raise Forbidden("You cannot delete your own account here; ask another administrator")
    user = db.scalar(select(m.User).where(m.User.id == user_id, m.User.org_id == admin.org_id).with_for_update())
    if user is None:
        raise NotFound("User not found")
    if user.email.endswith("@deleted.invalid"):
        raise Conflict("This account was already deleted")
    if user.role == "admin" and user.is_active:
        admins = db.scalar(
            select(func.count())
            .select_from(m.User)
            .where(m.User.org_id == admin.org_id, m.User.role == "admin", m.User.is_active, m.User.id != user.id)
        )
        if not admins:
            raise Conflict("The organisation needs at least one other active administrator")
    former = user.email
    user.email = f"deleted-{user.id.hex}@deleted.invalid"
    user.full_name = ""
    user.password_hash = hash_password(secrets.token_urlsafe(32))  # unknown to anyone
    user.is_active = False
    user.token_version += 1
    two_factor.clear(user)
    auth_service.revoke_all_for_user(db, user.id, "account_deleted")
    auth_service.invalidate_reset_links(db, user.id)
    db.execute(delete(m.Invitation).where(m.Invitation.org_id == admin.org_id, m.Invitation.email == former))
    return former


# --- organisation deletion -------------------------------------------------------------------------------------------


def deletion_status(db: Session, org_id: uuid.UUID) -> dict | None:
    raw = db.execute(select(m.Organization.settings).where(m.Organization.id == org_id)).scalar_one_or_none() or {}
    d = raw.get(DELETION_KEY)
    return d if isinstance(d, dict) and d.get("due_at") else None


def request_deletion(db: Session, admin: m.User, *, confirm_name: str, password: str) -> dict:
    org = db.scalar(select(m.Organization).where(m.Organization.id == admin.org_id).with_for_update())
    assert org is not None
    if confirm_name.strip() != org.name:
        raise ValidationFailed("Type the organisation's name exactly to confirm", code="confirmation_mismatch")
    if not verify_password(password, admin.password_hash):
        raise ValidationFailed("Current password is incorrect", code="invalid_current_password")
    if (org.settings or {}).get(DELETION_KEY):
        raise Conflict("Deletion is already scheduled")
    now = datetime.now(UTC)
    info = {"requested_at": now.isoformat(), "due_at": (now + DELETION_GRACE).isoformat(), "requested_by": admin.email}
    org.settings = {**(org.settings or {}), DELETION_KEY: info}
    return info


def cancel_deletion(db: Session, org_id: uuid.UUID) -> None:
    org = db.scalar(select(m.Organization).where(m.Organization.id == org_id).with_for_update())
    assert org is not None
    if not (org.settings or {}).get(DELETION_KEY):
        raise Conflict("No deletion is scheduled")
    org.settings = {k: v for k, v in (org.settings or {}).items() if k != DELETION_KEY}


def delete_organisation(db: Session, org_id: uuid.UUID) -> dict[str, int]:
    """Delete every stored file and row of one organisation. Irreversible; only the nightly task calls it."""
    storage = get_storage()
    keys = list(db.scalars(select(m.Dataset.storage_key).where(m.Dataset.org_id == org_id)))
    keys += list(db.scalars(select(m.ModelVersion.artifact_key).where(m.ModelVersion.org_id == org_id)))
    for job in db.scalars(select(m.Job).where(m.Job.org_id == org_id, m.Job.kind == "data_export")):
        if job.result and job.result.get("key"):
            keys.append(job.result["key"])
    files = 0
    for key in keys:
        try:
            storage.delete(key)
            files += 1
        except Exception:  # noqa: BLE001 - a missing file must not keep the data alive
            logger.warning("could not delete stored file during organisation deletion", exc_info=True)
    # Audit rows go first and explicitly: the runtime role may delete them but not update them, and removing the users
    # would otherwise try to null their actor references.
    audit_rows = db.execute(delete(m.AuditEvent).where(m.AuditEvent.org_id == org_id)).rowcount or 0  # type: ignore[attr-defined]
    db.execute(delete(m.Organization).where(m.Organization.id == org_id))  # everything else cascades
    return {"files": files, "audit_events": int(audit_rows)}


def purge_due_organisations(db: Session, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    done = []
    for org_id, raw in db.execute(select(m.Organization.id, m.Organization.settings)).all():
        d = (raw or {}).get(DELETION_KEY)
        if not isinstance(d, dict) or not d.get("due_at"):
            continue
        if datetime.fromisoformat(d["due_at"]) > now:
            continue
        counts = delete_organisation(db, org_id)
        # A record outside the organisation, which no longer exists, so operators can answer "what happened to X".
        audit.record(
            db,
            "organisation.deleted",
            org_id=None,
            resource_type="organization",
            resource_id=org_id,
            details={"requested_by": d.get("requested_by"), "requested_at": d.get("requested_at"), **counts},
        )
        db.commit()
        done.append(str(org_id))
    return done
