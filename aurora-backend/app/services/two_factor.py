"""Two-factor login with an authenticator app (TOTP) and single-use recovery codes (ADR-040)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import crypto, rate_limit, totp
from app.core.config import get_settings
from app.core.errors import Conflict, ValidationFailed
from app.core.security import verify_password
from app.db.models import Organization, User

RECOVERY_CODES = 10
POLICY_KEY = "require_two_factor"  # stored in Organization.settings next to the settings-service keys


def _hash_code(code: str) -> str:
    # Recovery codes carry 50 bits; a keyed hash means a database leak alone does not allow offline guessing.
    key = get_settings().secret_key.get_secret_value().encode()
    return hmac.new(key, b"aurora-recovery:" + _normalise(code).encode(), hashlib.sha256).hexdigest()


def _normalise(code: str) -> str:
    return code.strip().lower().replace("-", "").replace(" ", "")


def _new_recovery_codes(user: User) -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i
    codes = ["".join(secrets.choice(alphabet) for _ in range(10)) for _ in range(RECOVERY_CODES)]
    shown = [f"{c[:5]}-{c[5:]}" for c in codes]
    user.recovery_codes = [_hash_code(c) for c in codes]
    return shown


def _check_password(user: User, password: str) -> None:
    if not verify_password(password, user.password_hash):
        raise ValidationFailed("Current password is incorrect", code="invalid_current_password")


def org_requires(db: Session, org_id: uuid.UUID) -> bool:
    raw = db.execute(select(Organization.settings).where(Organization.id == org_id)).scalar_one_or_none() or {}
    return raw.get(POLICY_KEY) is True


def status(db: Session, user: User) -> dict:
    return {
        "enabled": user.two_factor_enabled,
        "enabled_at": user.totp_enabled_at.isoformat() if user.totp_enabled_at else None,
        "recovery_codes_left": len(user.recovery_codes or []) if user.two_factor_enabled else 0,
        "pending_setup": user.totp_pending is not None and not user.two_factor_enabled,
        "required_by_organisation": org_requires(db, user.org_id),
    }


def start_setup(user: User, password: str) -> dict:
    """A new secret to scan. It only takes effect once a code from it is confirmed."""
    if user.two_factor_enabled:
        raise Conflict("Two-factor login is already on. Turn it off first to move to a new device.")
    _check_password(user, password)
    secret = totp.new_secret()
    user.totp_pending = crypto.encrypt(secret)
    return {"secret": secret, "otpauth_uri": totp.provisioning_uri(secret, user.email)}


def confirm_setup(user: User, code: str) -> list[str]:
    if user.two_factor_enabled:
        raise Conflict("Two-factor login is already on")
    if user.totp_pending is None:
        raise ValidationFailed("Start the setup first")
    rate_limit.hit(f"2fa-confirm:{user.id}", 10, 300, fail_closed=True)
    secret = crypto.decrypt(user.totp_pending)
    step = totp.verify(secret, code)
    if step is None:
        raise ValidationFailed("That code does not match. Check the time on your phone and try the newest code.")
    user.totp_secret, user.totp_pending = user.totp_pending, None
    user.totp_last_step = step
    user.totp_enabled_at = datetime.now(UTC)
    return _new_recovery_codes(user)


def check_second_factor(user: User, code: str) -> str | None:
    """Verify a login code. Returns "totp" or "recovery_code" (which is then used up), or None."""
    if not user.two_factor_enabled or user.totp_secret is None:
        return None
    digits = code.strip().replace(" ", "")
    if digits.isdigit():
        step = totp.verify(crypto.decrypt(user.totp_secret), digits, last_step=user.totp_last_step)
        if step is None:
            return None
        user.totp_last_step = step
        return "totp"
    h = _hash_code(code)
    remaining = list(user.recovery_codes or [])
    for stored in remaining:
        if hmac.compare_digest(stored, h):
            remaining.remove(stored)
            user.recovery_codes = remaining
            return "recovery_code"
    return None


def _require_valid(user: User, code: str) -> str:
    rate_limit.hit(f"2fa-verify:{user.id}", 10, 300, fail_closed=True)
    how = check_second_factor(user, code)
    if how is None:
        raise ValidationFailed("That code is not valid", code="invalid_two_factor_code")
    return how


def disable(db: Session, user: User, password: str, code: str) -> None:
    if not user.two_factor_enabled:
        raise Conflict("Two-factor login is not on")
    if org_requires(db, user.org_id):
        raise Conflict("Your organisation requires two-factor login, so it cannot be turned off")
    _check_password(user, password)
    _require_valid(user, code)
    clear(user)


def regenerate_codes(user: User, code: str) -> list[str]:
    if not user.two_factor_enabled:
        raise Conflict("Two-factor login is not on")
    _require_valid(user, code)
    return _new_recovery_codes(user)


def clear(user: User) -> None:
    user.totp_secret = user.totp_pending = None
    user.totp_enabled_at = None
    user.totp_last_step = None
    user.recovery_codes = None


def set_policy(db: Session, actor: User, required: bool) -> bool:
    """Turn the organisation requirement on or off. Returns the previous value."""
    org = db.execute(select(Organization).where(Organization.id == actor.org_id).with_for_update()).scalar_one()
    before = (org.settings or {}).get(POLICY_KEY) is True
    if required and not actor.two_factor_enabled:
        raise Conflict("Turn on two-factor login for your own account before requiring it for everyone")
    org.settings = {**(org.settings or {}), POLICY_KEY: required}
    return before
