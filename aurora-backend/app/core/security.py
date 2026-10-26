"""Password hashing, JWT access tokens, refresh tokens and the role/permission model."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings


class Role(StrEnum):
    viewer = "viewer"
    operator = "operator"
    analyst = "analyst"
    admin = "admin"


class Perm(StrEnum):
    read = "read"  # dashboards, forecasts, products, alerts, simulations
    upload_data = "upload_data"
    manage_inventory = "manage_inventory"  # edit on-hand / lead-time settings
    acknowledge_alerts = "acknowledge_alerts"
    train_models = "train_models"
    manage_signals = "manage_signals"
    view_audit = "view_audit"
    manage_models = "manage_models"  # promote / rollback
    manage_users = "manage_users"
    delete_alerts = "delete_alerts"
    manage_integrations = "manage_integrations"
    view_ops = "view_ops"


_VIEWER = {Perm.read}
_OPERATOR = _VIEWER | {Perm.upload_data, Perm.manage_inventory, Perm.acknowledge_alerts}
_ANALYST = _OPERATOR | {Perm.train_models, Perm.manage_signals, Perm.view_audit, Perm.view_ops}
_ADMIN = set(Perm)

ROLE_PERMISSIONS: dict[Role, frozenset[Perm]] = {
    Role.viewer: frozenset(_VIEWER),
    Role.operator: frozenset(_OPERATOR),
    Role.analyst: frozenset(_ANALYST),
    Role.admin: frozenset(_ADMIN),
}


def has_perm(role: str, perm: Perm) -> bool:
    try:
        return perm in ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return False


# --- passwords -----------------------------------------------------------------------------

# A fixed hash used to equalise timing when the user does not exist.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt(rounds=4)).decode()


def hash_password(password: str) -> str:
    rounds = get_settings().bcrypt_rounds
    # bcrypt only uses the first 72 bytes; pre-hash so long passphrases are not silently truncated.
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        bcrypt.checkpw(_prehash(password), _DUMMY_HASH.encode())
        return False
    try:
        return bcrypt.checkpw(_prehash(password), hashed.encode())
    except ValueError:
        return False


def _prehash(password: str) -> bytes:
    import base64

    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def validate_password_strength(password: str) -> list[str]:
    problems = []
    if len(password) < 10:
        problems.append("must be at least 10 characters")
    if len(password) > 256:
        problems.append("must be at most 256 characters")
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if classes < 3:
        problems.append("must contain at least three of: lowercase, uppercase, digit, symbol")
    return problems


# --- tokens --------------------------------------------------------------------------------


def create_access_token(*, user_id: uuid.UUID, org_id: uuid.UUID, role: str, token_version: int) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=s.access_token_expire_minutes)
    claims = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "tv": token_version,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    token = jwt.encode(claims, s.secret_key.get_secret_value(), algorithm=s.jwt_algorithm)
    return token, s.access_token_expire_minutes * 60


def decode_access_token(token: str) -> dict[str, Any]:
    s = get_settings()
    claims = jwt.decode(
        token,
        s.secret_key.get_secret_value(),
        algorithms=[s.jwt_algorithm],
        audience=s.jwt_audience,
        issuer=s.jwt_issuer,
        options={"require": ["exp", "iat", "sub", "org", "role", "tv", "typ"]},
        leeway=10,
    )
    if claims.get("typ") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return claims


def new_refresh_token() -> tuple[str, str]:
    """Return (opaque token for the client, sha256 hash for the database)."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    # High-entropy random tokens do not need a slow KDF; a keyed hash prevents offline guessing from a DB leak.
    key = get_settings().secret_key.get_secret_value().encode()
    return hashlib.blake2b(raw.encode(), key=key[:64], digest_size=32).hexdigest()
