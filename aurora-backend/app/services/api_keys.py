"""API keys for other systems (ADR-043).

A key looks like `aur_<8 hex>_<43 url-safe chars>` (256 random bits after the prefix). Only its SHA-256 is stored;
the prefix is unique and finds the row, and the hash is compared in constant time. A key acts with its own role,
capped at its creator's current role, and stops working when the creator is deactivated or deleted, when it expires
or when it is revoked. Keys never reach account, user, key or organisation-deletion endpoints (see deps).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, NotFound, Unauthorized, ValidationFailed
from app.db import models as m

PREFIX = "aur_"
KEY_ROLES = ("viewer", "operator", "analyst")
RANK = {"viewer": 0, "operator": 1, "analyst": 2, "admin": 3}
MAX_ACTIVE = 25
MAX_EXPIRY_DAYS = 730
TOUCH_EVERY = timedelta(minutes=1)  # last_used_at is written at most this often per key


def looks_like_key(token: str) -> bool:
    return token.startswith(PREFIX)


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def key_out(k: m.ApiKey, creator_email: str | None, now: datetime | None = None) -> dict:
    now = now or _now()
    return {
        "id": str(k.id),
        "name": k.name,
        "prefix": k.prefix,
        "role": k.role,
        "created_at": k.created_at.isoformat(),
        "created_by_email": creator_email,
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "expires_at": k.expires_at.isoformat() if k.expires_at else None,
        "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
        "active": k.revoked_at is None and (k.expires_at is None or k.expires_at > now),
    }


def list_for_org(db: Session, org_id: uuid.UUID) -> list[dict]:
    rows = db.execute(
        select(m.ApiKey, m.User.email)
        .outerjoin(m.User, m.User.id == m.ApiKey.created_by)
        .where(m.ApiKey.org_id == org_id)
        .order_by(m.ApiKey.revoked_at.is_not(None), m.ApiKey.created_at.desc())
    ).all()
    now = _now()
    return [key_out(k, email, now) for k, email in rows]


def create(db: Session, creator: m.User, *, name: str, role: str, expires_in_days: int | None) -> tuple[m.ApiKey, str]:
    name = name.strip()
    if not name:
        raise ValidationFailed("Give the key a name")
    if role not in KEY_ROLES:
        raise ValidationFailed("A key's role must be viewer, operator or analyst")
    if RANK[role] > RANK[creator.role]:
        raise Forbidden("A key cannot have a higher role than yours")
    if expires_in_days is not None and not (1 <= expires_in_days <= MAX_EXPIRY_DAYS):
        raise ValidationFailed(f"Expiry must be between 1 and {MAX_EXPIRY_DAYS} days, or never")
    now = _now()
    active = db.scalar(
        select(func.count())
        .select_from(m.ApiKey)
        .where(
            m.ApiKey.org_id == creator.org_id,
            m.ApiKey.revoked_at.is_(None),
            (m.ApiKey.expires_at.is_(None)) | (m.ApiKey.expires_at > now),
        )
    )
    if (active or 0) >= MAX_ACTIVE:
        raise Conflict(f"An organisation can have at most {MAX_ACTIVE} active keys; revoke one first")
    for _ in range(5):  # the 32-bit prefix must be unique; a clash is very unlikely, so retry a few times
        prefix = f"{PREFIX}{secrets.token_hex(4)}"
        if db.scalar(select(m.ApiKey.id).where(m.ApiKey.prefix == prefix)) is None:
            break
    else:  # pragma: no cover
        raise Conflict("Could not create a key; try again")
    secret = f"{prefix}_{secrets.token_urlsafe(32)}"
    key = m.ApiKey(
        org_id=creator.org_id,
        name=name[:100],
        prefix=prefix,
        key_hash=_hash(secret),
        role=role,
        created_by=creator.id,
        expires_at=now + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    db.add(key)
    try:
        db.flush()
    except IntegrityError as exc:  # pragma: no cover - concurrent prefix clash
        raise Conflict("Could not create a key; try again") from exc
    return key, secret


def revoke(db: Session, org_id: uuid.UUID, key_id: uuid.UUID) -> m.ApiKey:
    key = db.scalar(select(m.ApiKey).where(m.ApiKey.id == key_id, m.ApiKey.org_id == org_id).with_for_update())
    if key is None:
        raise NotFound("API key not found")
    if key.revoked_at is not None:
        raise Conflict("This key was already revoked")
    key.revoked_at = _now()
    return key


def authenticate(db: Session, token: str) -> tuple[m.ApiKey, m.User, str]:
    """Return (key, creator, effective role) for a valid key, or raise Unauthorized with one generic message."""
    invalid = Unauthorized("Invalid or revoked API key")
    parts = token.split("_")
    if len(parts) < 3 or len(token) > 200:
        raise invalid
    prefix = f"{parts[0]}_{parts[1]}"
    key = db.scalar(select(m.ApiKey).where(m.ApiKey.prefix == prefix))
    if key is None or not hmac.compare_digest(key.key_hash, _hash(token)):
        raise invalid
    now = _now()
    if key.revoked_at is not None or (key.expires_at is not None and key.expires_at <= now):
        raise invalid
    user = db.get(m.User, key.created_by) if key.created_by else None
    if user is None or not user.is_active or user.org_id != key.org_id:
        raise invalid
    role = key.role if RANK[key.role] <= RANK[user.role] else user.role
    if key.last_used_at is None or now - key.last_used_at >= TOUCH_EVERY:
        db.execute(update(m.ApiKey).where(m.ApiKey.id == key.id).values(last_used_at=now))
        db.commit()
    return key, user, role
