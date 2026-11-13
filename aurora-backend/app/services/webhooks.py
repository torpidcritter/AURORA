"""Outgoing webhooks: signed event notifications to addresses an administrator registers (ADR-043).

`emit` writes one delivery row per subscribed endpoint in the caller's transaction, so an event is recorded exactly
when the change it reports commits (an outbox). The worker sends due deliveries every minute, signs each body with
the endpoint's secret, and retries failures with growing gaps. An endpoint that keeps failing is switched off.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core import crypto, outbound
from app.core.errors import AppError, Conflict, NotFound, ValidationFailed
from app.db import models as m

EVENTS: dict[str, str] = {
    "alert.opened": "A new alert was raised (stockout risk, drift, accuracy and others)",
    "purchase_order.status_changed": "A purchase order was approved, sent, received or cancelled",
    "job.finished": "A background job (training, batch forecast, export and others) succeeded or failed",
    "model.promoted": "A model version became the active one",
}
TEST_EVENT = "ping"
MAX_ENDPOINTS = 10
# Seconds to wait after attempt n (1-based) fails. Six attempts span about 15 hours.
BACKOFF_S = (60, 300, 1800, 7200, 43200)
MAX_ATTEMPTS = len(BACKOFF_S) + 1
DISABLE_AFTER = 30  # consecutive failed attempts before an endpoint is switched off
BATCH = 100
SIGNATURE_TOLERANCE_S = 300  # receivers should reject older timestamps (documented in docs/WEBHOOKS.md)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _hint(url: str) -> str:
    parts = url.split("/")
    return "/".join(parts[:3]) + "/…"  # host only: a webhook path often carries a token


def endpoint_out(e: m.WebhookEndpoint) -> dict:
    return {
        "id": str(e.id),
        "url_hint": e.url_hint,
        "description": e.description,
        "events": list(e.events or []),
        "active": e.active,
        "disabled_reason": e.disabled_reason,
        "consecutive_failures": e.consecutive_failures,
        "last_success_at": _iso(e.last_success_at),
        "last_failure_at": _iso(e.last_failure_at),
        "created_at": e.created_at.isoformat(),
    }


def delivery_out(d: m.WebhookDelivery) -> dict:
    return {
        "id": str(d.id),
        "endpoint_id": str(d.endpoint_id),
        "event": d.event,
        "status": d.status,
        "attempts": d.attempts,
        "last_status_code": d.last_status_code,
        "last_error": d.last_error,
        "next_attempt_at": _iso(d.next_attempt_at) if d.status == "pending" else None,
        "created_at": d.created_at.isoformat(),
        "delivered_at": _iso(d.delivered_at),
    }


def _check_events(events: list[str]) -> list[str]:
    chosen = sorted(set(events))
    if not chosen:
        raise ValidationFailed("Choose at least one event")
    unknown = [e for e in chosen if e not in EVENTS]
    if unknown:
        raise ValidationFailed(f"Unknown event: {', '.join(unknown)}")
    return chosen


def _new_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(32)}"


def list_endpoints(db: Session, org_id: uuid.UUID) -> list[m.WebhookEndpoint]:
    return list(
        db.scalars(
            select(m.WebhookEndpoint).where(m.WebhookEndpoint.org_id == org_id).order_by(m.WebhookEndpoint.created_at)
        )
    )


def get_endpoint(db: Session, org_id: uuid.UUID, endpoint_id: uuid.UUID, *, lock: bool = False) -> m.WebhookEndpoint:
    stmt = select(m.WebhookEndpoint).where(m.WebhookEndpoint.id == endpoint_id, m.WebhookEndpoint.org_id == org_id)
    e = db.scalar(stmt.with_for_update() if lock else stmt)
    if e is None:
        raise NotFound("Webhook endpoint not found")
    return e


def create_endpoint(
    db: Session, org_id: uuid.UUID, user_id: uuid.UUID, *, url: str, events: list[str], description: str = ""
) -> tuple[m.WebhookEndpoint, str]:
    count = db.scalar(select(func.count()).select_from(m.WebhookEndpoint).where(m.WebhookEndpoint.org_id == org_id))
    if (count or 0) >= MAX_ENDPOINTS:
        raise Conflict(f"An organisation can have at most {MAX_ENDPOINTS} webhook endpoints")
    safe = outbound.check_url(url)
    secret = _new_secret()
    e = m.WebhookEndpoint(
        org_id=org_id,
        url_encrypted=crypto.encrypt(safe),
        url_hint=_hint(safe)[:300],
        secret_encrypted=crypto.encrypt(secret),
        description=description.strip()[:200],
        events=_check_events(events),
        active=True,
        created_by=user_id,
    )
    db.add(e)
    db.flush()
    return e, secret


def update_endpoint(db: Session, org_id: uuid.UUID, endpoint_id: uuid.UUID, changes: dict) -> m.WebhookEndpoint:
    e = get_endpoint(db, org_id, endpoint_id, lock=True)
    if changes.get("url") is not None:
        safe = outbound.check_url(changes["url"])
        e.url_encrypted = crypto.encrypt(safe)
        e.url_hint = _hint(safe)[:300]
    if changes.get("events") is not None:
        e.events = _check_events(changes["events"])
    if changes.get("description") is not None:
        e.description = changes["description"].strip()[:200]
    if changes.get("active") is not None:
        e.active = bool(changes["active"])
        if e.active:  # switched back on: start counting failures afresh
            e.disabled_reason = None
            e.consecutive_failures = 0
        else:
            e.disabled_reason = "Turned off by an administrator"
    return e


def rotate_secret(db: Session, org_id: uuid.UUID, endpoint_id: uuid.UUID) -> str:
    e = get_endpoint(db, org_id, endpoint_id, lock=True)
    secret = _new_secret()
    e.secret_encrypted = crypto.encrypt(secret)
    return secret


def delete_endpoint(db: Session, org_id: uuid.UUID, endpoint_id: uuid.UUID) -> None:
    e = get_endpoint(db, org_id, endpoint_id, lock=True)
    db.delete(e)


def _payload(delivery_id: uuid.UUID, event: str, data: dict, when: datetime) -> dict:
    return {"id": str(delivery_id), "type": event, "created_at": when.isoformat(), "data": data}


def emit(db: Session, org_id: uuid.UUID, event: str, data: dict[str, Any]) -> int:
    """Queue `event` for every active endpoint of the organisation that subscribed to it. Part of the caller's
    transaction: nothing is sent if the caller rolls back. Returns how many deliveries were queued."""
    assert event in EVENTS, event
    endpoints = db.scalars(
        select(m.WebhookEndpoint).where(m.WebhookEndpoint.org_id == org_id, m.WebhookEndpoint.active.is_(True))
    ).all()
    now = _now()
    n = 0
    for e in endpoints:
        if event not in (e.events or []):
            continue
        did = uuid.uuid4()
        db.add(
            m.WebhookDelivery(
                id=did,
                org_id=org_id,
                endpoint_id=e.id,
                event=event,
                payload=_payload(did, event, data, now),
                status="pending",
                next_attempt_at=now,
            )
        )
        n += 1
    return n


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify(
    secret: str, header: str, body: bytes, *, now: int | None = None, tolerance: int = SIGNATURE_TOLERANCE_S
) -> bool:
    """Reference check for receivers (and tests): constant-time compare and a freshness window."""
    try:
        fields = dict(part.split("=", 1) for part in header.split(","))
        ts = int(fields["t"])
    except (ValueError, KeyError):
        return False
    if abs((now if now is not None else int(time.time())) - ts) > tolerance:
        return False
    expected = sign(secret, ts, body).split("v1=", 1)[1]
    return hmac.compare_digest(expected, fields.get("v1", ""))


def _send(e: m.WebhookEndpoint, d: m.WebhookDelivery, client: httpx.Client | None) -> tuple[int | None, str | None]:
    """POST one delivery. Returns (status code, error); error is None on a 2xx answer."""
    body = json.dumps(d.payload, separators=(",", ":"), default=str).encode()
    try:
        url = outbound.check_url(crypto.decrypt(e.url_encrypted))
    except AppError as exc:
        return None, f"Address refused: {exc.message}"[:500]
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Aurora-Webhooks/1",
        "Aurora-Event": d.event,
        "Aurora-Delivery": str(d.id),
        "Aurora-Signature": sign(crypto.decrypt(e.secret_encrypted), int(time.time()), body),
    }
    c = client or httpx.Client(timeout=outbound.TIMEOUT_S, follow_redirects=False)
    try:
        r = c.post(url, content=body, headers=headers)
        if r.is_redirect:
            return r.status_code, "Redirects are not followed"
        if 200 <= r.status_code < 300:
            return r.status_code, None
        return r.status_code, f"Receiver answered HTTP {r.status_code}"
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {exc}"[:500]
    finally:
        if client is None:
            c.close()


def attempt(db: Session, d: m.WebhookDelivery, e: m.WebhookEndpoint, client: httpx.Client | None = None) -> bool:
    """Try one delivery now and record the outcome (the caller commits). Returns True on success."""
    now = _now()
    code, error = _send(e, d, client)
    d.attempts += 1
    d.last_status_code = code
    if error is None:
        d.status = "succeeded"
        d.delivered_at = now
        d.last_error = None
        d.next_attempt_at = None
        e.consecutive_failures = 0
        e.last_success_at = now
        return True
    d.last_error = error[:500]
    e.consecutive_failures += 1
    e.last_failure_at = now
    if d.attempts >= MAX_ATTEMPTS or d.event == TEST_EVENT:
        d.status = "failed"
        d.next_attempt_at = None
    else:
        d.next_attempt_at = now + timedelta(seconds=BACKOFF_S[d.attempts - 1])
    if e.active and e.consecutive_failures >= DISABLE_AFTER:
        e.active = False
        e.disabled_reason = (
            f"Switched off after {DISABLE_AFTER} failed attempts in a row; fix the receiver and turn it on"
        )
    return False


def deliver_due(db: Session, *, limit: int = BATCH, client: httpx.Client | None = None) -> dict[str, int]:
    """Send deliveries whose time has come. Rows are locked with SKIP LOCKED so several workers never double-send."""
    now = _now()
    rows = db.execute(
        select(m.WebhookDelivery, m.WebhookEndpoint)
        .join(m.WebhookEndpoint, m.WebhookEndpoint.id == m.WebhookDelivery.endpoint_id)
        .where(m.WebhookDelivery.status == "pending", m.WebhookDelivery.next_attempt_at <= now)
        .order_by(m.WebhookDelivery.next_attempt_at)
        .limit(limit)
        .with_for_update(of=m.WebhookDelivery, skip_locked=True)
    ).all()
    out = {"sent": 0, "failed": 0, "skipped": 0}
    for d, e in rows:
        if not e.active:  # switched off since the event: keep it, so it can be retried by hand
            d.status = "failed"
            d.last_error = "Endpoint is turned off"
            d.next_attempt_at = None
            out["skipped"] += 1
            continue
        out["sent" if attempt(db, d, e, client) else "failed"] += 1
    db.commit()
    return out


def send_test(
    db: Session, org_id: uuid.UUID, endpoint_id: uuid.UUID, client: httpx.Client | None = None
) -> m.WebhookDelivery:
    """Send a `ping` event straight away (one attempt, no retries)."""
    e = get_endpoint(db, org_id, endpoint_id, lock=True)
    did = uuid.uuid4()
    now = _now()
    d = m.WebhookDelivery(
        id=did,
        org_id=org_id,
        endpoint_id=e.id,
        event=TEST_EVENT,
        payload=_payload(did, TEST_EVENT, {"message": "Test event from Aurora"}, now),
        status="pending",
        next_attempt_at=None,
    )
    db.add(d)
    db.flush()
    attempt(db, d, e, client)
    return d


def retry(db: Session, org_id: uuid.UUID, delivery_id: uuid.UUID) -> m.WebhookDelivery:
    d = db.scalar(
        select(m.WebhookDelivery)
        .where(m.WebhookDelivery.id == delivery_id, m.WebhookDelivery.org_id == org_id)
        .with_for_update()
    )
    if d is None:
        raise NotFound("Delivery not found")
    if d.status != "failed":
        raise Conflict("Only failed deliveries can be retried")
    e = db.get(m.WebhookEndpoint, d.endpoint_id)
    if e is None or not e.active:
        raise Conflict("Turn the endpoint on before retrying")
    d.status = "pending"
    d.next_attempt_at = _now()
    d.attempts = min(d.attempts, MAX_ATTEMPTS - 1)  # one more automatic attempt at least
    return d


def list_deliveries(
    db: Session, org_id: uuid.UUID, *, endpoint_id: uuid.UUID | None, limit: int, offset: int
) -> tuple[list[m.WebhookDelivery], int]:
    base = select(m.WebhookDelivery).where(m.WebhookDelivery.org_id == org_id)
    if endpoint_id is not None:
        base = base.where(m.WebhookDelivery.endpoint_id == endpoint_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(m.WebhookDelivery.created_at.desc()).limit(limit).offset(offset)).all()
    return list(rows), total


def purge_old(db: Session, now: datetime | None = None, days: int = 30) -> int:
    """Delete finished deliveries older than `days` (pending ones are kept until they finish)."""
    cutoff = (now or _now()) - timedelta(days=days)
    res = db.execute(
        delete(m.WebhookDelivery).where(m.WebhookDelivery.status != "pending", m.WebhookDelivery.created_at < cutoff)
    )
    return res.rowcount or 0
