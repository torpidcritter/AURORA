"""Alert creation (deduplicated, escalating), acknowledgement, resolution and idempotent delivery."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound
from app.core.metrics import ACTIVE_ALERTS
from app.db.models import Alert, AlertDelivery

logger = logging.getLogger("aurora.alerts")
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
MAX_DELIVERY_ATTEMPTS = 5


def upsert(
    db: Session,
    *,
    org_id: uuid.UUID,
    kind: str,
    severity: str,
    title: str,
    message: str,
    dedup_key: str,
    product_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> uuid.UUID:
    """Insert, or update the existing unresolved alert with the same dedup key. Severity only escalates
    automatically (it never silently downgrades an alert someone is looking at)."""
    stmt = insert(Alert).values(
        id=uuid.uuid4(),
        org_id=org_id,
        product_id=product_id,
        kind=kind,
        severity=severity,
        title=title[:300],
        message=message,
        dedup_key=dedup_key,
        status="open",
        details=details or {},
    )
    rank = text(
        "CASE WHEN (CASE excluded.severity WHEN 'low' THEN 0 WHEN 'medium' THEN 1 WHEN 'high' THEN 2 ELSE 3 END) > "
        "(CASE alerts.severity WHEN 'low' THEN 0 WHEN 'medium' THEN 1 WHEN 'high' THEN 2 ELSE 3 END) "
        "THEN excluded.severity ELSE alerts.severity END"
    )
    escalated = text(
        "CASE WHEN (CASE excluded.severity WHEN 'low' THEN 0 WHEN 'medium' THEN 1 WHEN 'high' THEN 2 ELSE 3 END) > "
        "(CASE alerts.severity WHEN 'low' THEN 0 WHEN 'medium' THEN 1 WHEN 'high' THEN 2 ELSE 3 END) "
        "THEN 'open' ELSE alerts.status END"
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["org_id", "dedup_key"],
        index_where=text("status <> 'resolved'"),
        set_={
            "severity": rank,
            "status": escalated,
            "title": stmt.excluded.title,
            "message": stmt.excluded.message,
            "details": stmt.excluded.details,
            "updated_at": func.now(),
        },
    ).returning(Alert.id)
    return db.execute(stmt).scalar_one()


def resolve_where_cleared(db: Session, org_id: uuid.UUID, prefix: str, still_active: set[str]) -> int:
    """Auto-resolve alerts of one family whose condition no longer holds."""
    q = select(Alert).where(Alert.org_id == org_id, Alert.status != "resolved", Alert.dedup_key.startswith(prefix))
    n = 0
    for a in db.scalars(q).all():
        if a.dedup_key not in still_active:
            a.status = "resolved"
            a.resolved_at = datetime.now(UTC)
            n += 1
    return n


def get(db: Session, org_id: uuid.UUID, alert_id: uuid.UUID) -> Alert:
    a = db.get(Alert, alert_id)
    if a is None or a.org_id != org_id:
        raise NotFound("Alert not found")
    return a


def acknowledge(db: Session, org_id: uuid.UUID, alert_id: uuid.UUID, user_id: uuid.UUID) -> Alert:
    res = db.execute(
        update(Alert)
        .where(Alert.id == alert_id, Alert.org_id == org_id, Alert.status == "open")
        .values(status="acknowledged", acknowledged_at=func.now(), acknowledged_by=user_id)
        .returning(Alert.id)
    ).first()
    a = get(db, org_id, alert_id)
    if res is None and a.status != "acknowledged":
        raise Conflict(f"Alert is {a.status}")
    db.refresh(a)
    return a


def resolve(db: Session, org_id: uuid.UUID, alert_id: uuid.UUID) -> Alert:
    a = get(db, org_id, alert_id)
    if a.status != "resolved":
        a.status = "resolved"
        a.resolved_at = datetime.now(UTC)
    return a


def refresh_gauge(db: Session) -> None:
    ACTIVE_ALERTS.set(db.scalar(select(func.count()).select_from(Alert).where(Alert.status != "resolved")) or 0)


def channels() -> list[str]:
    return ["log"] + (["webhook"] if get_settings().alert_webhook_url else [])


def dispatch(db: Session, sender: httpx.Client | None = None) -> dict[str, int]:
    """Deliver open high/critical alerts. A unique (alert_id, channel) row is claimed *before* sending, so retries
    or concurrent dispatchers never deliver the same alert twice to a channel. Webhook delivery is at-least-once
    from the receiver's perspective only if we crash between send and commit; the alert id is sent as an
    idempotency key for that case."""
    stats = {"delivered": 0, "failed": 0, "skipped": 0}
    alerts = db.scalars(
        select(Alert)
        .where(Alert.status == "open", Alert.severity.in_(("high", "critical")))
        .order_by(Alert.created_at)
        .limit(500)
    ).all()
    for a in alerts:
        for ch in channels():
            claimed = db.execute(
                insert(AlertDelivery)
                .values(id=uuid.uuid4(), alert_id=a.id, channel=ch, status="pending")
                .on_conflict_do_nothing(constraint="uq_alert_delivery_channel")
                .returning(AlertDelivery.id)
            ).first()
            db.commit()
            row = db.scalar(
                select(AlertDelivery)
                .where(AlertDelivery.alert_id == a.id, AlertDelivery.channel == ch)
                .with_for_update(skip_locked=True)
            )
            stale_pending = (
                row is not None
                and row.status == "pending"
                and (datetime.now(UTC) - row.created_at).total_seconds() > 600
            )  # claimer crashed mid-send
            if (
                row is None
                or row.status in ("delivered", "dead")
                or (claimed is None and row.status != "failed" and not stale_pending)
            ):
                stats["skipped"] += 1
                db.commit()
                continue
            row.attempts += 1
            try:
                _send(ch, a, sender)
                row.status = "delivered"
                row.delivered_at = datetime.now(UTC)
                stats["delivered"] += 1
            except Exception as exc:  # noqa: BLE001
                row.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
                row.status = "dead" if row.attempts >= MAX_DELIVERY_ATTEMPTS else "failed"
                stats["failed"] += 1
            db.commit()
    return stats


def _send(channel: str, a: Alert, sender: httpx.Client | None) -> None:
    payload = {
        "id": str(a.id),
        "org_id": str(a.org_id),
        "kind": a.kind,
        "severity": a.severity,
        "title": a.title,
        "message": a.message,
        "created_at": a.created_at.isoformat(),
    }
    if channel == "log":
        logger.warning("ALERT %s", a.title, extra={"extra_fields": payload})
        return
    url = get_settings().alert_webhook_url
    assert url
    client = sender or httpx.Client(timeout=5.0)
    r = client.post(url, json=payload, headers={"Idempotency-Key": str(a.id)})
    r.raise_for_status()
