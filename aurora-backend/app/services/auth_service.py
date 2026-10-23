"""Authentication: login with lockout, refresh-token rotation with reuse detection, logout, user admin."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, Unauthorized, ValidationFailed
from app.core.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    validate_password_strength,
    verify_password,
)
from app.db.models import RefreshToken, User
from app.services import audit

logger = logging.getLogger("aurora.auth")

REUSE_GRACE_SECONDS = 20


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


def login(db: Session, email: str, password: str, ip: str | None, ua: str | None) -> IssuedTokens:
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
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = _now()
    tokens = _issue(db, user, None, ip, ua)
    audit.record(db, "auth.login", org_id=user.org_id, actor_id=user.id, actor_email=user.email, ip=ip)
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
