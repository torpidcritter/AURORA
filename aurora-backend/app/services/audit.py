"""Audit trail for sensitive actions. Details are redacted before storage."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import redact, request_id_var
from app.db.models import AuditEvent


def record(
    db: Session,
    action: str,
    *,
    org_id: uuid.UUID | None,
    actor_id: uuid.UUID | None = None,
    actor_email: str | None = None,
    resource_type: str | None = None,
    resource_id: object = None,
    outcome: str = "success",
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add an audit event to the current transaction (the caller commits)."""
    ev = AuditEvent(
        org_id=org_id,
        actor_user_id=actor_id,
        actor_email=actor_email,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        outcome=outcome,
        ip=ip,
        request_id=request_id_var.get(),
        details=redact(details or {}),  # type: ignore[arg-type]
    )
    db.add(ev)
    return ev


def record_for(db: Session, principal: Any, action: str, **kw: Any) -> AuditEvent:
    return record(
        db,
        action,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        actor_email=principal.email,
        ip=principal.ip,
        **kw,
    )
