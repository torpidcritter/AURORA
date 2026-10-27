"""Authentication: login with lockout, refresh-token rotation with reuse detection, logout, user admin and
admin-issued one-time password reset links."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.config import get_settings
from app.core.errors import AppError, Conflict, Forbidden, NotFound, Unauthorized, ValidationFailed
from app.core.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    validate_password_strength,
    verify_password,
)
from app.db.models import PasswordResetToken, RefreshToken, User
from app.services import audit, two_factor

logger = logging.getLogger("aurora.auth")

REUSE_GRACE_SECONDS = 20
# The token travels in the URL fragment, which browsers never send to a server or put in a Referer header.
RESET_PATH = "/reset-password#token="


@dataclass
class IssuedResetLink:
    reset_url: str
    expires_at: datetime


@dataclass
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int
    user: User


def _now() -> datetime:
    return datetime.now(UTC)


def _issue(db: Session, user: User, family_id: uuid.UUID | None, ip: str | None, ua: str | None) -> IssuedTokens:
    s = get_settings()
    access, expires_in = create_access_token(
        user_id=user.id, org_id=user.org_id, role=user.role, token_version=user.token_version
    )
    raw, digest = new_refresh_token()
    ttl = timedelta(days=s.refresh_token_expire_days)
    db.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id or uuid.uuid4(),
            token_hash=digest,
            expires_at=_now() + ttl,
            user_agent=(ua or "")[:300] or None,
            ip=ip,
        )
    )
    return IssuedTokens(access, raw, expires_in, int(ttl.total_seconds()), user)


def login(
    db: Session, email: str, password: str, ip: str | None, ua: str | None, otp: str | None = None
) -> IssuedTokens:
    s = get_settings()
    email_n = email.strip().lower()
    rate_limit.hit(f"login:ip:{ip}", s.login_rate_limit_per_minute * 3, 60, fail_closed=True)
    rate_limit.hit(f"login:email:{email_n}", s.login_rate_limit_per_minute, 60, fail_closed=True)

    user = db.scalar(select(User).where(User.email == email_n).with_for_update())
    generic = Unauthorized("Invalid email or password")
    if user is None:
        verify_password(password, None)  # equalise timing
        audit.record(
            db,
            "auth.login",
            org_id=None,
            actor_email=email_n,
            outcome="failure",
            ip=ip,
            details={"reason": "unknown_user"},
        )
        db.commit()
        raise generic
    if user.locked_until and user.locked_until > _now():
        audit.record(
            db,
            "auth.login",
            org_id=user.org_id,
            actor_id=user.id,
            actor_email=user.email,
            outcome="failure",
            ip=ip,
            details={"reason": "locked"},
        )
        db.commit()
        raise Unauthorized(
            "Account temporarily locked after repeated failures. Try again later.", code="account_locked"
        )
    if not verify_password(password, user.password_hash) or not user.is_active:
        if user.is_active:
            user.failed_login_count += 1
            if user.failed_login_count >= s.login_lockout_threshold:
                user.locked_until = _now() + timedelta(minutes=s.login_lockout_minutes)
                user.failed_login_count = 0
        audit.record(
            db,
            "auth.login",
            org_id=user.org_id,
            actor_id=user.id,
            actor_email=user.email,
            outcome="failure",
            ip=ip,
            details={"reason": "bad_credentials" if user.is_active else "inactive"},
        )
        db.commit()
        raise generic
    if user.two_factor_enabled:
        if not otp:
            # The password was right; ask for the second factor without counting a failure. The per-account rate
            # limit above still bounds password guessing through this answer.
            db.commit()
            raise Unauthorized("Enter the code from your authenticator app", code="two_factor_required")
        how = two_factor.check_second_factor(user, otp)
        if how is None:
            user.failed_login_count += 1
            if user.failed_login_count >= s.login_lockout_threshold:
                user.locked_until = _now() + timedelta(minutes=s.login_lockout_minutes)
                user.failed_login_count = 0
            audit.record(
                db,
                "auth.login",
                org_id=user.org_id,
                actor_id=user.id,
                actor_email=user.email,
                outcome="failure",
                ip=ip,
                details={"reason": "bad_second_factor"},
            )
            db.commit()
            raise Unauthorized("That code is not valid", code="invalid_two_factor_code")
        if how == "recovery_code":
            audit.record(
                db,
                "auth.recovery_code_used",
                org_id=user.org_id,
                actor_id=user.id,
                actor_email=user.email,
                ip=ip,
                details={"remaining": len(user.recovery_codes or [])},
            )
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = _now()
    tokens = _issue(db, user, None, ip, ua)
    audit.record(
        db,
        "auth.login",
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        ip=ip,
        details={"two_factor": user.two_factor_enabled},
    )
    db.commit()
    return tokens


def refresh(db: Session, raw: str, ip: str | None, ua: str | None) -> IssuedTokens:
    digest = hash_refresh_token(raw)
    tok = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == digest).with_for_update())
    if tok is None:
        raise Unauthorized("Invalid refresh token")
    user = db.get(User, tok.user_id)
    now = _now()
    if tok.revoked_at is not None or tok.used_at is not None:
        if (
            tok.revoked_at is None
            and tok.used_at is not None
            and (now - tok.used_at).total_seconds() < REUSE_GRACE_SECONDS
        ):
            # Concurrent refresh from the same client (e.g. two tabs). Do not punish, but do not re-issue.
            raise Unauthorized("Refresh token already rotated", code="refresh_superseded")
        _revoke_family(db, tok.family_id, "reuse_detected")
        if user is not None:
            audit.record(
                db,
                "auth.refresh_reuse_detected",
                org_id=user.org_id,
                actor_id=user.id,
                actor_email=user.email,
                outcome="failure",
                ip=ip,
                details={"family": str(tok.family_id)},
            )
        db.commit()
        logger.warning("refresh token reuse detected for family %s", tok.family_id)
        raise Unauthorized("Refresh token reuse detected; all sessions in this family were revoked", code="token_reuse")
    if tok.expires_at <= now:
        raise Unauthorized("Refresh token expired")
    if user is None or not user.is_active:
        _revoke_family(db, tok.family_id, "user_inactive")
        db.commit()
        raise Unauthorized("Session is no longer valid")
    tok.used_at = now
    tokens = _issue(db, user, tok.family_id, ip, ua)
    db.commit()
    return tokens


def _revoke_family(db: Session, family_id: uuid.UUID, reason: str) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now(), revoked_reason=reason)
    )


def revoke_all_for_user(db: Session, user_id: uuid.UUID, reason: str) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now(), revoked_reason=reason)
    )


def logout(db: Session, raw: str, ip: str | None) -> None:
    tok = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw)))
    if tok is None:
        return  # idempotent
    _revoke_family(db, tok.family_id, "logout")
    user = db.get(User, tok.user_id)
    if user:
        audit.record(db, "auth.logout", org_id=user.org_id, actor_id=user.id, actor_email=user.email, ip=ip)
    db.commit()


def _check_password(pw: str) -> None:
    problems = validate_password_strength(pw)
    if problems:
        raise ValidationFailed("Password does not meet requirements", details=problems)


def create_user(db: Session, *, org_id: uuid.UUID, email: str, password: str, role: str, full_name: str = "") -> User:
    _check_password(password)
    email_n = email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email_n)):
        raise Conflict("A user with that email already exists")
    user = User(org_id=org_id, email=email_n, full_name=full_name, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    return user


def get_org_user(db: Session, org_id: uuid.UUID, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None or user.org_id != org_id:
        raise NotFound("User not found")
    return user


def change_password(db: Session, user: User, current: str, new: str) -> None:
    if not verify_password(current, user.password_hash):
        # 422, not 401: the session itself is valid, and clients treat 401 as "log out"
        raise ValidationFailed("Current password is incorrect", code="invalid_current_password")
    set_password(db, user, new)


def set_password(db: Session, user: User, new: str) -> None:
    _check_password(new)
    user.password_hash = hash_password(new)
    user.token_version += 1
    revoke_all_for_user(db, user.id, "password_changed")
    invalidate_reset_links(db, user.id)


# --- admin-issued one-time reset links -------------------------------------------------------


def hash_reset_token(raw: str) -> str:
    # 256-bit random tokens: a plain SHA-256 is enough, offline guessing from a database leak is infeasible.
    return hashlib.sha256(raw.encode()).hexdigest()


def invalidate_reset_links(db: Session, user_id: uuid.UUID) -> None:
    """Expire every unused reset link of a user (a new link, a password change or deactivation supersedes them)."""
    now = _now()
    db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .values(expires_at=now)
        .execution_options(synchronize_session=False)
    )


def create_reset_link(
    db: Session, *, org_id: uuid.UUID, user_id: uuid.UUID, created_by: uuid.UUID
) -> tuple[User, IssuedResetLink]:
    """Issue a one-time link for a user of the caller's organisation. Only the token's hash is stored; the caller shows
    the link once. Earlier unused links for that user stop working."""
    # Row lock: two admins issuing links at once must not leave two valid links behind.
    user = db.scalar(
        select(User)
        .where(User.id == user_id, User.org_id == org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise NotFound("User not found")
    if user.id == created_by:
        # A stolen admin session must not be able to take over the account without the current password.
        raise Forbidden("Use Change password for your own account", code="own_account")
    if not user.is_active:
        raise Conflict("Reactivate this user before issuing a reset link", code="user_inactive")
    s = get_settings()
    invalidate_reset_links(db, user.id)
    raw = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(minutes=s.password_reset_ttl_minutes)
    db.add(
        PasswordResetToken(
            org_id=user.org_id,
            user_id=user.id,
            token_hash=hash_reset_token(raw),
            created_by=created_by,
            expires_at=expires_at,
        )
    )
    db.flush()
    return user, IssuedResetLink(f"{s.frontend_url.rstrip('/')}{RESET_PATH}{raw}", expires_at)


def _audit_reset_failure(db: Session, tok: PasswordResetToken | None, ip: str | None, reason: str) -> None:
    audit.record(
        db,
        "auth.password_reset",
        org_id=tok.org_id if tok else None,
        resource_type="user" if tok else None,
        resource_id=tok.user_id if tok else None,
        outcome="failure",
        ip=ip,
        details={"reason": reason},
    )
    db.commit()


def _reset_failed(db: Session, tok: PasswordResetToken | None, ip: str | None, reason: str) -> AppError:
    """Audit a failed reset and return the generic error: the caller learns nothing about why the link is unusable."""
    _audit_reset_failure(db, tok, ip, reason)
    return AppError(
        "This reset link is invalid, has expired or was already used. Ask an administrator for a new one.",
        code="invalid_reset_token",
    )


def reset_password_with_token(db: Session, raw: str, new_password: str, ip: str | None) -> User:
    """Set a new password with a one-time link. On success the link is spent, every session of the user ends and any
    other outstanding link stops working, all in one transaction."""
    s = get_settings()
    rate_limit.hit(f"password_reset:ip:{ip}", s.login_rate_limit_per_minute, 60, fail_closed=True)
    tok = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_reset_token(raw)))
    if tok is None:
        raise _reset_failed(db, None, ip, "unknown_token")
    # Serialises with concurrent resets, new links and deactivation of the same user.
    user = db.scalar(
        select(User)
        .where(User.id == tok.user_id, User.org_id == tok.org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is not None:
        db.refresh(tok)  # we may have waited for another transaction on this user: judge the link as it is now
    now = _now()
    if user is None or not user.is_active:
        raise _reset_failed(db, tok, ip, "inactive_user")
    if tok.used_at is not None:
        raise _reset_failed(db, tok, ip, "used")
    if tok.expires_at <= now:
        raise _reset_failed(db, tok, ip, "expired")
    problems = validate_password_strength(new_password)
    if problems:
        # The link is not spent, so the user can try again with a stronger password.
        _audit_reset_failure(db, tok, ip, "weak_password")
        raise ValidationFailed("Password does not meet requirements", details=problems)
    # Single use even if two requests got this far: only one conditional UPDATE can match the row.
    claimed = db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.id == tok.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .values(used_at=now)
        .returning(PasswordResetToken.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if claimed is None:
        raise _reset_failed(db, tok, ip, "used")
    set_password(db, user, new_password)  # bumps token_version, revokes refresh tokens, expires other links
    user.failed_login_count = 0
    user.locked_until = None
    audit.record(
        db,
        "auth.password_reset",
        org_id=user.org_id,
        actor_id=user.id,
        actor_email=user.email,
        resource_type="user",
        resource_id=user.id,
        ip=ip,
        details={"issued_by": str(tok.created_by) if tok.created_by else None},
    )
    db.commit()
    return user
