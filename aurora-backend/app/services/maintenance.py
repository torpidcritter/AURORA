"""Housekeeping: remove rows that have outlived their purpose. Never touches business data or the audit log
(the audit log has its own retention task)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import OAuthState, PasswordResetToken, RefreshToken, WebhookEvent

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
