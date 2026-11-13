"""Scheduled digests: a short summary of stockout risk, overstock, forecast accuracy, alerts and purchasing, sent
daily or weekly to the app, a webhook, Slack or e-mail (ADR-036).

Every digest is stored (``DigestRun``), so the in-app copy exists whatever the channel and a failed delivery can be
inspected. Schedules are claimed with ``FOR UPDATE SKIP LOCKED`` and moved to their next slot before anything is sent,
so two workers never send the same slot and a crash skips one digest rather than repeating it.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import crypto, outbound
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import DIGEST_SECTIONS, Alert, DigestRun, DigestSchedule, Organization, PurchaseOrder
from app.services import accuracy_service, inventory_service, mailer, overstock_service
from app.services.product_service import org_as_of
from app.services.purchasing_service import order_number

log = logging.getLogger("aurora.digests")

SECTION_TITLES = {
    "stockouts": "Stockout risk",
    "overstock": "Overstock",
    "accuracy": "Forecast accuracy",
    "alerts": "Alerts",
    "purchasing": "Purchasing",
}
TOP_N = 10
SLACK_HOSTS = ("hooks.slack.com",)
MAX_RECIPIENTS = 20
OPEN_DELIVERY = ("sent", "partially_received")


def _now() -> datetime:
    return datetime.now(UTC)


# ---- schedule timing ----------------------------------------------------------------------------------------------


def next_run(frequency: str, hour_utc: int, weekday: int | None, after: datetime) -> datetime:
    """The first slot strictly after ``after``: every day at ``hour_utc``, or on ``weekday`` (0 = Monday) at it."""
    slot = after.astimezone(UTC).replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if slot <= after:
        slot += timedelta(days=1)
    if frequency == "weekly":
        assert weekday is not None
        slot += timedelta(days=(weekday - slot.weekday()) % 7)
    return slot


# ---- targets ------------------------------------------------------------------------------------------------------


def _check_target(channel: str, target: str | None) -> tuple[str | None, str | None]:
    """Validate a channel's target. Returns (value to encrypt, masked hint)."""
    if channel == "in_app":
        if target:
            raise ValidationFailed("In-app digests have no target")
        return None, None
    if not target or not target.strip():
        raise ValidationFailed("Enter where to send the digest")
    target = target.strip()
    if channel == "email":
        if not get_settings().mail_configured:
            raise ValidationFailed("E-mail is not configured on this server (MAIL_BACKEND=smtp and SMTP_HOST)")
        addrs = [a.strip() for a in target.replace(";", ",").split(",") if a.strip()]
        if not addrs or len(addrs) > MAX_RECIPIENTS:
            raise ValidationFailed(f"Enter between 1 and {MAX_RECIPIENTS} e-mail addresses")
        for a in addrs:
            local, _, domain = a.partition("@")
            if not local or "." not in domain or any(c.isspace() for c in a) or len(a) > 320:
                raise ValidationFailed(f"{a} is not an e-mail address")
        return ", ".join(addrs), ", ".join(_mask_email(a) for a in addrs)[:200]
    url = outbound.check_url(target, allowed_hosts=SLACK_HOSTS if channel == "slack" else None)
    parts = url.split("/")
    return url, "/".join(parts[:3]) + "/…"  # host only: the path of a webhook URL is its secret


def _mask_email(a: str) -> str:
    local, _, domain = a.partition("@")
    return f"{local[:1]}…@{domain}"


# ---- schedule CRUD ------------------------------------------------------------------------------------------------


def schedule_out(s: DigestSchedule) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "frequency": s.frequency,
        "weekday": s.weekday,
        "hour_utc": s.hour_utc,
        "sections": list(s.sections),
        "channel": s.channel,
        "target_hint": s.target_hint,
        "is_active": s.is_active,
        "next_run_at": s.next_run_at.isoformat(),
        "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
        "version": s.version,
        "created_at": s.created_at.isoformat(),
    }


def list_schedules(db: Session, org_id: uuid.UUID) -> list[DigestSchedule]:
    return list(
        db.scalars(select(DigestSchedule).where(DigestSchedule.org_id == org_id).order_by(DigestSchedule.created_at))
    )


def get_schedule(db: Session, org_id: uuid.UUID, schedule_id: uuid.UUID, *, lock: bool = False) -> DigestSchedule:
    q = select(DigestSchedule).where(DigestSchedule.id == schedule_id, DigestSchedule.org_id == org_id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    s = db.scalar(q)
    if s is None:
        raise NotFound("Digest schedule not found")
    return s


def _check_timing(frequency: str, weekday: int | None) -> None:
    if frequency == "weekly" and weekday is None:
        raise ValidationFailed("Choose the day of the week for a weekly digest")
    if frequency == "daily" and weekday is not None:
        raise ValidationFailed("A daily digest has no day of the week")


def create_schedule(db: Session, org_id: uuid.UUID, user_id: uuid.UUID, v: dict) -> DigestSchedule:
    _check_timing(v["frequency"], v.get("weekday"))
    value, hint = _check_target(v["channel"], v.get("target"))
    s = DigestSchedule(
        org_id=org_id,
        name=v["name"].strip(),
        frequency=v["frequency"],
        weekday=v.get("weekday"),
        hour_utc=v["hour_utc"],
        sections=list(dict.fromkeys(v["sections"])),
        channel=v["channel"],
        target_encrypted=crypto.encrypt(value) if value else None,
        target_hint=hint,
        is_active=True,
        next_run_at=next_run(v["frequency"], v["hour_utc"], v.get("weekday"), _now()),
        created_by=user_id,
    )
    db.add(s)
    db.flush()
    return s


def update_schedule(db: Session, org_id: uuid.UUID, schedule_id: uuid.UUID, changes: dict) -> DigestSchedule:
    s = get_schedule(db, org_id, schedule_id, lock=True)
    expected = changes.pop("expected_version")
    if s.version != expected:
        raise Conflict("This schedule was changed by someone else; reload and try again", code="version_conflict")
    if "target" in changes and "channel" not in changes:
        changes["channel"] = s.channel
    if "channel" in changes:
        value, hint = _check_target(changes["channel"], changes.pop("target", None))
        s.channel = changes.pop("channel")
        s.target_encrypted = crypto.encrypt(value) if value else None
        s.target_hint = hint
    frequency = changes.get("frequency", s.frequency)
    weekday = changes["weekday"] if "weekday" in changes else s.weekday
    if frequency == "daily" and "frequency" in changes and "weekday" not in changes:
        weekday = None
    _check_timing(frequency, weekday)
    for k in ("name", "hour_utc", "is_active"):
        if k in changes:
            setattr(s, k, changes[k].strip() if k == "name" else changes[k])
    if "sections" in changes:
        s.sections = list(dict.fromkeys(changes["sections"]))
    s.frequency, s.weekday = frequency, weekday
    s.next_run_at = next_run(s.frequency, s.hour_utc, s.weekday, _now())
    s.version += 1
    return s


def delete_schedule(db: Session, org_id: uuid.UUID, schedule_id: uuid.UUID) -> None:
    db.delete(get_schedule(db, org_id, schedule_id, lock=True))


# ---- content ------------------------------------------------------------------------------------------------------


def build(db: Session, org_id: uuid.UUID, sections: list[str], period_days: int) -> dict:
    """The digest's content as data. Every number comes from the same services as the app's pages."""
    as_of = org_as_of(db, org_id)
    since = _now() - timedelta(days=period_days)
    out: dict[str, Any] = {"as_of": as_of.isoformat() if as_of else None, "period_days": period_days, "sections": {}}
    for sec in DIGEST_SECTIONS:
        if sec not in sections:
            continue
        out["sections"][sec] = _SECTION_BUILDERS[sec](db, org_id, since)
    return out


def _stockouts(db: Session, org_id: uuid.UUID, since: datetime) -> dict:
    rows = [r for r in inventory_service.risk_matrix(db, org_id) if r.get("status") == "ok"]
    levels = Counter(r["risk_level"] for r in rows)
    at_risk = [r for r in rows if r["risk_level"] in ("critical", "high")]
    return {
        "critical": levels.get("critical", 0),
        "high": levels.get("high", 0),
        "products": len(rows),
        "items": [
            {
                "sku": r["sku"],
                "name": r["name"],
                "risk_level": r["risk_level"],
                "days_until_stockout": r.get("days_until_stockout"),
                "recommended_order_qty": r.get("recommended_order_qty"),
            }
            for r in at_risk[:TOP_N]
        ],
    }


def _overstock(db: Session, org_id: uuid.UUID, since: datetime) -> dict:
    rep = overstock_service.report(db, org_id, limit=5)
    return {
        **rep["summary"],
        "items": [
            {
                "sku": i["sku"],
                "name": i["name"],
                "excess_units": i.get("excess_units"),
                "excess_value": i.get("excess_value"),
            }
            for i in rep["items"]
        ],
    }


def _accuracy(db: Session, org_id: uuid.UUID, since: datetime) -> dict:
    acc = accuracy_service.live_accuracy(db, org_id)
    return {
        "window_days": acc.get("window_days"),
        "wape": acc.get("wape"),
        "bias": acc.get("bias"),
        "points": acc.get("points", 0),
        "sufficient": acc.get("sufficient", False),
        "worst": [
            {"sku": r["sku"], "wape": r["wape"]} for r in (acc.get("by_product") or [])[:5] if r.get("wape") is not None
        ],
    }


def _alerts(db: Session, org_id: uuid.UUID, since: datetime) -> dict:
    open_by = dict(
        db.execute(
            select(Alert.severity, func.count())
            .where(Alert.org_id == org_id, Alert.status == "open")
            .group_by(Alert.severity)
        )
        .tuples()
        .all()
    )
    new = db.scalars(
        select(Alert)
        .where(Alert.org_id == org_id, Alert.created_at >= since)
        .order_by(Alert.created_at.desc())
        .limit(TOP_N)
    ).all()
    return {
        "open": {k: int(open_by.get(k, 0)) for k in ("critical", "high", "medium", "low")},
        "new": [{"severity": a.severity, "title": a.title} for a in new],
    }


def _purchasing(db: Session, org_id: uuid.UUID, since: datetime) -> dict:
    as_of = org_as_of(db, org_id) or _now().date()
    today = max(as_of, _now().date())
    orders = db.scalars(
        select(PurchaseOrder).where(
            PurchaseOrder.org_id == org_id,
            PurchaseOrder.status.in_(("draft", "approved", "sent", "partially_received")),
        )
    ).all()
    late = [
        o for o in orders if o.status in ("sent", "partially_received") and o.expected_date and o.expected_date < today
    ]
    return {
        "drafts": sum(o.status == "draft" for o in orders),
        "awaiting_send": sum(o.status == "approved" for o in orders),
        "open_deliveries": sum(o.status in ("sent", "partially_received") for o in orders),
        "late": [{"number": order_number(o.number), "expected_date": o.expected_date.isoformat()} for o in late][
            :TOP_N
        ],
    }


_SECTION_BUILDERS = {
    "stockouts": _stockouts,
    "overstock": _overstock,
    "accuracy": _accuracy,
    "alerts": _alerts,
    "purchasing": _purchasing,
}


# ---- rendering ----------------------------------------------------------------------------------------------------


def _n(v: Any, digits: int = 0) -> str:
    if v is None:
        return "—"
    return f"{v:,.{digits}f}"


def render_lines(title: str, content: dict) -> list[str]:
    """Plain-text lines (used for e-mail and Slack). No HTML or markup from data is interpreted by receivers."""
    lines = [title, f"Data as of {content.get('as_of') or 'no sales yet'}", ""]
    s = content["sections"]
    if "stockouts" in s:
        x = s["stockouts"]
        lines.append(f"{SECTION_TITLES['stockouts']}: {x['critical']} critical, {x['high']} high of {x['products']}")
        for i in x["items"]:
            days = "unknown" if i["days_until_stockout"] is None else f"{i['days_until_stockout']:.1f} days"
            lines.append(
                f"  - {i['sku']} ({i['risk_level']}): stockout in {days}, order {_n(i['recommended_order_qty'])}"
            )
    if "overstock" in s:
        x = s["overstock"]
        lines.append(
            f"{SECTION_TITLES['overstock']}: {x['overstock']} overstocked, {x['dead']} dead stock, "
            f"excess value {_n(x['excess_value'], 2)}"
        )
        for i in x["items"]:
            lines.append(f"  - {i['sku']}: {_n(i['excess_units'])} excess units")
    if "accuracy" in s:
        x = s["accuracy"]
        if x["sufficient"]:
            lines.append(f"{SECTION_TITLES['accuracy']}: WAPE {_n(x['wape'], 1)}% over {x['window_days']} days")
            for i in x["worst"]:
                lines.append(f"  - {i['sku']}: WAPE {_n(i['wape'], 1)}%")
        else:
            lines.append(f"{SECTION_TITLES['accuracy']}: not enough forecasts with actual sales yet")
    if "alerts" in s:
        x = s["alerts"]
        o = x["open"]
        lines.append(
            f"{SECTION_TITLES['alerts']}: open {o['critical']} critical, {o['high']} high, {o['medium']} medium"
        )
        for a in x["new"]:
            lines.append(f"  - new ({a['severity']}): {a['title']}")
    if "purchasing" in s:
        x = s["purchasing"]
        lines.append(
            f"{SECTION_TITLES['purchasing']}: {x['drafts']} drafts, {x['awaiting_send']} approved to send, "
            f"{x['open_deliveries']} awaiting delivery, {len(x['late'])} late"
        )
        for o in x["late"]:
            lines.append(f"  - {o['number']} expected {o['expected_date']}")
    return lines


def _slack_text(lines: list[str]) -> str:
    # Slack treats <, > and & as control characters; escape so product names cannot inject links or mentions.
    return "\n".join(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") for line in lines)


# ---- delivery -----------------------------------------------------------------------------------------------------


def _deliver(s: DigestSchedule | None, channel: str, title: str, content: dict, sender: httpx.Client | None) -> None:
    if channel == "in_app":
        return
    assert s is not None and s.target_encrypted
    target = crypto.decrypt(s.target_encrypted)
    link = f"{get_settings().frontend_url.rstrip('/')}/digests"
    lines = [*render_lines(title, content), "", f"Open in Aurora: {link}"]
    if channel == "email":
        mailer.send([a.strip() for a in target.split(",")], title, "\n".join(lines))
    elif channel == "slack":
        outbound.post_json(target, {"text": _slack_text(lines)}, client=sender)
    else:
        outbound.post_json(target, {"title": title, "link": link, **content}, client=sender)


def _title(org: Organization | None, s: DigestSchedule | None, when: datetime) -> str:
    name = s.name if s else "Digest preview"
    return f"{name} · {org.name if org else 'Aurora'} · {when.date().isoformat()}"


def run(
    db: Session,
    org_id: uuid.UUID,
    s: DigestSchedule,
    *,
    trigger: str = "schedule",
    sender: httpx.Client | None = None,
) -> DigestRun:
    when = _now()
    title = _title(db.get(Organization, org_id), s, when)
    content = build(db, org_id, list(s.sections), 7 if s.frequency == "weekly" else 1)
    status, error = ("in_app" if s.channel == "in_app" else "delivered"), None
    try:
        _deliver(s, s.channel, title, content, sender)
    except Exception as exc:  # noqa: BLE001 - any delivery failure is recorded on the run and shown in the app
        status, error = "failed", f"{type(exc).__name__}: {str(exc)[:400]}"
        log.warning("digest delivery failed", extra={"extra_fields": {"schedule_id": str(s.id), "error": error}})
    r = DigestRun(
        org_id=org_id,
        schedule_id=s.id,
        title=title,
        channel=s.channel,
        status=status,
        error=error,
        content=content,
        trigger=trigger,
    )
    db.add(r)
    s.last_run_at = when
    db.flush()
    return r


def run_due(db: Session, *, limit: int = 50, sender: httpx.Client | None = None) -> dict[str, int]:
    """Send every digest whose slot has come. Each schedule is claimed, moved to its next slot and committed before
    sending, so concurrent workers skip it and a crash during sending does not resend that slot."""
    stats = {"sent": 0, "failed": 0}
    for _ in range(limit):
        s = db.scalar(
            select(DigestSchedule)
            .where(DigestSchedule.is_active, DigestSchedule.next_run_at <= _now())
            .order_by(DigestSchedule.next_run_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if s is None:
            break
        s.next_run_at = next_run(s.frequency, s.hour_utc, s.weekday, max(_now(), s.next_run_at))
        db.commit()
        try:
            r = run(db, s.org_id, s, sender=sender)
            stats["failed" if r.status == "failed" else "sent"] += 1
            db.commit()
        except Exception:  # noqa: BLE001 - one organisation's broken data must not stop the others
            db.rollback()
            stats["failed"] += 1
            log.exception("digest build failed", extra={"extra_fields": {"schedule_id": str(s.id)}})
    return stats


def run_out(r: DigestRun, *, with_content: bool = False) -> dict:
    out = {
        "id": str(r.id),
        "schedule_id": str(r.schedule_id) if r.schedule_id else None,
        "title": r.title,
        "channel": r.channel,
        "status": r.status,
        "error": r.error,
        "trigger": r.trigger,
        "created_at": r.created_at.isoformat(),
    }
    if with_content:
        out["content"] = r.content
        out["text"] = render_lines(r.title, r.content)
    return out


def list_runs(db: Session, org_id: uuid.UUID, limit: int, offset: int) -> tuple[list[DigestRun], int]:
    base = select(DigestRun).where(DigestRun.org_id == org_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(DigestRun.created_at.desc()).limit(limit).offset(offset)).all()
    return list(rows), int(total)


def get_run(db: Session, org_id: uuid.UUID, run_id: uuid.UUID) -> DigestRun:
    r = db.scalar(select(DigestRun).where(DigestRun.id == run_id, DigestRun.org_id == org_id))
    if r is None:
        raise NotFound("Digest not found")
    return r
