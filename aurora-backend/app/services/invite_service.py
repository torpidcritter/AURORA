"""Team invitations: an administrator invites someone by e-mail address and role; they choose their own password
through a one-time link (ADR-041)."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.config import get_settings
from app.core.errors import AppError, Conflict, NotFound
from app.db.models import Invitation, Organization, User
from app.services import audit, auth_service, mailer

logger = logging.getLogger("aurora.invites")

INVITE_TTL = timedelta(days=7)
INVITE_PATH = "/accept-invite#token="  # the token stays in the fragment, so it is never sent to a server or a Referer


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()  # 256-bit random tokens, like reset links


def _now() -> datetime:
    return datetime.now(UTC)


def invite_out(inv: Invitation) -> dict:
    now = _now()
    state = (
        "accepted"
        if inv.accepted_at
        else "revoked"
        if inv.revoked_at
        else "expired"
        if inv.expires_at <= now
        else "pending"
    )
    return {
        "id": str(inv.id),
        "email": inv.email,
        "role": inv.role,
        "state": state,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
        "expires_at": inv.expires_at.isoformat(),
        "accepted_at": inv.accepted_at.isoformat() if inv.accepted_at else None,
    }


def create(
    db: Session, *, org_id: uuid.UUID, email: str, role: str, created_by: uuid.UUID
) -> tuple[Invitation, str, bool]:
    """Returns the invitation, its link (shown once) and whether it was e-mailed."""
    rate_limit.hit(f"invites:{org_id}", 30, 3600)
    email_n = email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email_n)):
        raise Conflict("Someone with that e-mail address already has an account", code="user_exists")
    # A new invitation replaces any pending one for the same address in this organisation.
    db.execute(
        update(Invitation)
        .where(
            Invitation.org_id == org_id,
            Invitation.email == email_n,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
        .execution_options(synchronize_session=False)
    )
    raw = secrets.token_urlsafe(32)
    inv = Invitation(
        org_id=org_id,
        email=email_n,
        role=role,
        token_hash=_hash(raw),
        created_by=created_by,
        expires_at=_now() + INVITE_TTL,
    )
    db.add(inv)
    db.flush()
    s = get_settings()
    link = f"{s.frontend_url.rstrip('/')}{INVITE_PATH}{raw}"
    emailed = False
    if s.mail_configured:
        org_name = db.scalar(select(Organization.name).where(Organization.id == org_id)) or "Aurora"
        try:
            mailer.send(
                [email_n],
                f"You are invited to {org_name} on Aurora",
                f"You have been invited to join {org_name} on Aurora as {role}.\n\n"
                f"Choose your password here (the link works once and expires in 7 days):\n{link}\n\n"
                "If you did not expect this, ignore this message.",
            )
            emailed = True
        except Exception:  # noqa: BLE001 - the admin still gets the link to pass on
            logger.warning("invitation e-mail failed", exc_info=True)
    return inv, link, emailed


def list_for_org(db: Session, org_id: uuid.UUID) -> list[dict]:
    rows = db.scalars(
        select(Invitation).where(Invitation.org_id == org_id).order_by(Invitation.created_at.desc()).limit(200)
    ).all()
    return [invite_out(i) for i in rows]


def revoke(db: Session, org_id: uuid.UUID, invite_id: uuid.UUID) -> Invitation:
    inv = db.scalar(select(Invitation).where(Invitation.id == invite_id, Invitation.org_id == org_id).with_for_update())
    if inv is None:
        raise NotFound("Invitation not found")
    if inv.accepted_at:
        raise Conflict("This invitation was already accepted; deactivate the user instead")
    if inv.revoked_at is None:
        inv.revoked_at = _now()
    return inv


def _usable(db: Session, raw: str, ip: str | None, *, lock: bool) -> Invitation:
    rate_limit.hit(f"invite-token:ip:{ip}", 20, 300, fail_closed=True)
    q = select(Invitation).where(Invitation.token_hash == _hash(raw))
    inv = db.scalar(q.with_for_update() if lock else q)
    if inv is None or inv.accepted_at or inv.revoked_at or inv.expires_at <= _now():
        raise AppError(
            "This invitation link is invalid, already used or expired. Ask your administrator for a new one.",
            status_code=400,
            code="invalid_invitation",
        )
    return inv


def inspect(db: Session, raw: str, ip: str | None) -> dict:
    inv = _usable(db, raw, ip, lock=False)
    org_name = db.scalar(select(Organization.name).where(Organization.id == inv.org_id))
    return {"email": inv.email, "role": inv.role, "org_name": org_name, "expires_at": inv.expires_at.isoformat()}


def accept(db: Session, raw: str, *, full_name: str, password: str, ip: str | None) -> User:
    inv = _usable(db, raw, ip, lock=True)
    user = auth_service.create_user(
        db, org_id=inv.org_id, email=inv.email, password=password, role=inv.role, full_name=full_name.strip()
    )
    inv.accepted_at = _now()
    inv.user_id = user.id
    audit.record(
        db,
        "user.invite_accepted",
        org_id=inv.org_id,
        actor_id=user.id,
        actor_email=user.email,
        resource_type="user",
        resource_id=user.id,
        ip=ip,
        details={"role": inv.role, "invitation_id": str(inv.id)},
    )
    db.commit()
    return user
