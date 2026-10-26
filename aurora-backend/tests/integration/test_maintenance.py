"""Housekeeping: expired auth/OAuth rows and old webhook ids are purged; live ones are kept."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db.models import AuditEvent, OAuthState, RefreshToken, WebhookEvent
from app.services import maintenance


def _token(user_id: uuid.UUID, expires: datetime, **kw) -> RefreshToken:
    return RefreshToken(
        user_id=user_id,
        family_id=uuid.uuid4(),
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        expires_at=expires,
        **kw,
    )


def test_purge_expired_removes_only_dead_rows(client, org, db):
    now = datetime.now(UTC)
    uid = org.users["admin"]
    org.token("viewer")  # a real login creates a live refresh token that must survive
    live_before = db.scalar(select(func.count()).select_from(RefreshToken))
    db.add_all(
        [
            _token(uid, now - timedelta(days=3)),  # long expired -> purged
            _token(uid, now - timedelta(hours=2)),  # just expired, inside grace -> kept
            _token(
                uid, now + timedelta(days=5), revoked_at=now, revoked_reason="rotated"
            ),  # needed for reuse detection
            OAuthState(
                state="old", org_id=org.org_id, shop_domain="a.myshopify.com", expires_at=now - timedelta(hours=3)
            ),
            OAuthState(
                state="new", org_id=org.org_id, shop_domain="a.myshopify.com", expires_at=now + timedelta(minutes=5)
            ),
            WebhookEvent(
                provider="shopify",
                external_id="old",
                topic="orders/create",
                status="processed",
                received_at=now - timedelta(days=40),
            ),
            WebhookEvent(
                provider="shopify",
                external_id="recent",
                topic="orders/create",
                status="processed",
                received_at=now - timedelta(days=2),
            ),
            AuditEvent(org_id=org.org_id, action="test.event", outcome="success", created_at=now - timedelta(days=90)),
        ]
    )
    db.commit()

    counts = maintenance.purge_expired(db, now=now)

    assert counts == {"refresh_tokens": 1, "oauth_states": 1, "webhook_events": 1}
    assert db.scalar(select(func.count()).select_from(RefreshToken)) == live_before + 2
    assert set(db.scalars(select(OAuthState.state))) == {"new"}
    assert set(db.scalars(select(WebhookEvent.external_id))) == {"recent"}
    assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "test.event")) == 1
    # The viewer's session still works after the purge.
    assert client.get("/auth/me", headers=org.h("viewer")).status_code == 200


def test_purge_task_is_scheduled():
    from app.workers.celery_app import celery_app

    sched = {v["task"] for v in celery_app.conf.beat_schedule.values()}
    assert "aurora.purge_expired" in sched
