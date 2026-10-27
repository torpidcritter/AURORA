"""Housekeeping: remove rows that have outlived their purpose. Never touches business data or the audit log
(the audit log has its own retention task)."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import AuditEvent, OAuthState, Organization, PasswordResetToken, RefreshToken, WebhookEvent
from app.services import audit, settings_service

# Revoked or rotated refresh tokens are kept until they expire: reuse detection needs them. After expiry a reused
# token is rejected anyway, so one extra day of grace is plenty.
REFRESH_GRACE = timedelta(days=1)
OAUTH_STATE_GRACE = timedelta(hours=1)
# Spent or expired reset links are kept a day so a support question about "my link did not work" can be answered.
RESET_LINK_GRACE = timedelta(days=1)


def purge_expired(db: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    s = get_settings()
    counts = {}
    res = db.execute(delete(RefreshToken).where(RefreshToken.expires_at < now - REFRESH_GRACE))
    counts["refresh_tokens"] = int(res.rowcount or 0)  # type: ignore[attr-defined]
    res = db.execute(delete(OAuthState).where(OAuthState.expires_at < now - OAUTH_STATE_GRACE))
    counts["oauth_states"] = int(res.rowcount or 0)  # type: ignore[attr-defined]
    cutoff = now - RESET_LINK_GRACE
    res = db.execute(
        delete(PasswordResetToken).where(
            or_(PasswordResetToken.expires_at < cutoff, PasswordResetToken.used_at < cutoff)
        )
    )
    counts["password_reset_tokens"] = int(res.rowcount or 0)  # type: ignore[attr-defined]
    # Only finished deliveries; one still "processing" is left for investigation.
    res = db.execute(
        delete(WebhookEvent).where(
            WebhookEvent.received_at < now - timedelta(days=s.webhook_event_retention_days),
            WebhookEvent.status != "processing",
        )
    )
    counts["webhook_events"] = int(res.rowcount or 0)  # type: ignore[attr-defined]
    db.commit()
    return counts


def purge_audit(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Delete audit events older than each organisation's retention (ADR-039).

    Organisations without their own value, and events with no organisation, use ``AUDIT_RETENTION_DAYS``. Each
    organisation that lost events gets one system audit event saying how many and up to when, so the gap is explained.
    """
    now = now or datetime.now(UTC)
    default_days = get_settings().audit_retention_days
    by_days: dict[int, list] = defaultdict(list)
    for org_id, raw in db.execute(select(Organization.id, Organization.settings)).all():
        by_days[settings_service.resolve(raw).audit_retention_days].append(org_id)
    counts: dict[str, int] = {}
    for days, org_ids in by_days.items():
        cutoff = now - timedelta(days=days)
        rows = db.execute(
            delete(AuditEvent)
            .where(AuditEvent.org_id.in_(org_ids), AuditEvent.created_at < cutoff)
            .returning(AuditEvent.org_id)
        ).all()
        per_org: dict = defaultdict(int)
        for (oid,) in rows:
            per_org[oid] += 1
        for oid, n in per_org.items():
            counts[str(oid)] = n
            audit.record(
                db,
                "audit.retention_purge",
                org_id=oid,
                resource_type="audit_log",
                details={"deleted": n, "before": cutoff.isoformat(), "retention_days": days},
            )
    res = db.execute(
        delete(AuditEvent).where(
            AuditEvent.org_id.is_(None), AuditEvent.created_at < now - timedelta(days=default_days)
        )
    )
    counts["no_organisation"] = int(res.rowcount or 0)  # type: ignore[attr-defined]
    db.commit()
    return counts
