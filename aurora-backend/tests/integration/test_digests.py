"""Scheduled digests: schedules, safe outbound targets, content, delivery to each channel and the scheduler."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core import outbound
from app.core.config import get_settings
from app.db.models import Alert, AuditEvent, DigestRun, DigestSchedule, Product, PurchaseOrder, SalesRecord
from app.services import digest_service

HOOK = "https://hooks.example.com/services/T000/B000/secret-path"
SLACK = "https://hooks.slack.com/services/T000/B000/secret-path"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """Test hosts resolve to a public documentation address unless a test says otherwise."""
    monkeypatch.setattr(outbound, "_resolve", lambda host: ["93.184.215.14"])


def _create(client, org, role="admin", **body):
    payload = {"name": "Morning summary", "sections": ["alerts", "purchasing"], **body}
    return client.post("/digests/schedules", json=payload, headers=org.h(role))


def _capture(status=200):
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(status)

    return seen, httpx.Client(transport=httpx.MockTransport(handler))


def _seed(db, org_id):
    db.add(Alert(org_id=org_id, kind="stockout", severity="critical", title="SKU-1 <@here> runs out", dedup_key="a"))
    db.add(Alert(org_id=org_id, kind="drift", severity="medium", title="Drift on SKU-2", dedup_key="b"))
    db.add(PurchaseOrder(org_id=org_id, number=7, status="sent", expected_date=date(2020, 1, 1)))
    db.add(PurchaseOrder(org_id=org_id, number=8, status="draft"))
    db.commit()


def test_next_run_slots():
    t = datetime(2026, 10, 27, 7, 30, tzinfo=UTC)  # a Tuesday
    assert digest_service.next_run("daily", 8, None, t) == datetime(2026, 10, 27, 8, tzinfo=UTC)
    assert digest_service.next_run("daily", 7, None, t) == datetime(2026, 10, 28, 7, tzinfo=UTC)
    assert digest_service.next_run("weekly", 7, 0, t) == datetime(2026, 11, 2, 7, tzinfo=UTC)  # next Monday
    assert digest_service.next_run("weekly", 9, 1, t) == datetime(2026, 10, 27, 9, tzinfo=UTC)  # later today
    assert digest_service.next_run("weekly", 7, 1, t) == datetime(2026, 11, 3, 7, tzinfo=UTC)


def test_in_app_schedule_lifecycle(client, org, other_org):
    r = _create(client, org, frequency="weekly", weekday=0, hour_utc=6)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["channel"] == "in_app" and s["target_hint"] is None and s["next_run_at"].endswith("06:00:00+00:00")
    assert [x["id"] for x in client.get("/digests/schedules", headers=org.h("viewer")).json()] == [s["id"]]
    assert client.get("/digests/schedules", headers=other_org.h()).json() == []
    assert _create(client, org, frequency="weekly").status_code == 422  # weekly needs a day
    assert _create(client, org, frequency="daily", weekday=2).status_code == 422
    assert _create(client, org, channel="in_app", target=HOOK).status_code == 422

    up = client.patch(
        f"/digests/schedules/{s['id']}", json={"expected_version": 1, "frequency": "daily"}, headers=org.h()
    )
    assert up.status_code == 200 and up.json()["weekday"] is None and up.json()["version"] == 2
    stale = client.patch(f"/digests/schedules/{s['id']}", json={"expected_version": 1, "name": "x"}, headers=org.h())
    assert stale.status_code == 409
    assert (
        client.patch(f"/digests/schedules/{s['id']}", json={"expected_version": 2}, headers=other_org.h()).status_code
        == 404
    )
    assert client.delete(f"/digests/schedules/{s['id']}", headers=org.h()).status_code == 204
    assert client.get("/digests/schedules", headers=org.h()).json() == []


def test_outbound_targets_are_checked_and_kept_secret(client, org, db, monkeypatch):
    assert _create(client, org, channel="webhook", target="http://hooks.example.com/x").status_code == 422
    assert _create(client, org, channel="webhook", target="https://user:pw@hooks.example.com/x").status_code == 422
    assert _create(client, org, channel="webhook", target="https://hooks.example.com:8443/x").status_code == 422
    assert _create(client, org, channel="slack", target=HOOK).status_code == 422  # Slack must be hooks.slack.com
    for private in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "fd00::1"):
        monkeypatch.setattr(outbound, "_resolve", lambda host, ip=private: [ip])
        r = _create(client, org, channel="webhook", target=HOOK)
        assert r.status_code == 422 and "private" in r.json()["error"]["message"], private
    monkeypatch.setattr(outbound, "_resolve", lambda host: ["93.184.215.14"])

    r = _create(client, org, channel="webhook", target=HOOK)
    assert r.status_code == 201, r.text
    assert r.json()["target_hint"] == "https://hooks.example.com/…"
    row = db.get(DigestSchedule, uuid.UUID(r.json()["id"]))
    assert row.target_encrypted and "secret-path" not in row.target_encrypted
    events = db.scalars(select(AuditEvent).where(AuditEvent.action.like("digest.%"))).all()
    assert events and all("secret-path" not in json.dumps(e.details) for e in events)
    assert "secret-path" not in json.dumps(client.get("/digests/schedules", headers=org.h()).json())


def test_email_needs_a_configured_server(client, org, monkeypatch):
    assert _create(client, org, channel="email", target="ops@example.com").status_code == 422
    s = get_settings()
    monkeypatch.setattr(s, "mail_backend", "smtp")
    monkeypatch.setattr(s, "smtp_host", "smtp.example.com")
    assert _create(client, org, channel="email", target="not-an-address").status_code == 422
    r = _create(client, org, channel="email", target="ops@example.com; buyer@example.com")
    assert r.status_code == 201 and r.json()["target_hint"] == "o…@example.com, b…@example.com"

    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent.append({"host": host, "port": port})

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context):
            sent[-1]["tls"] = True

        def send_message(self, msg):
            sent[-1]["msg"] = msg

    monkeypatch.setattr("app.services.mailer.smtplib.SMTP", FakeSMTP)
    out = client.post(f"/digests/schedules/{r.json()['id']}/send", headers=org.h()).json()
    assert out["status"] == "delivered" and out["trigger"] == "manual"
    msg = sent[0]["msg"]
    assert sent[0]["tls"] and msg["To"] == "ops@example.com, buyer@example.com"
    assert msg["Subject"].startswith("Morning summary") and "Open in Aurora" in msg.get_content()


def test_scheduler_sends_due_digests_once(client, org, db):
    _seed(db, org.org_id)
    hook = _create(client, org, channel="webhook", target=HOOK).json()
    slack = _create(client, org, channel="slack", target=SLACK).json()
    in_app = _create(client, org).json()
    later = _create(client, org).json()
    past = datetime.now(UTC) - timedelta(minutes=1)
    for sid in (hook["id"], slack["id"], in_app["id"]):
        db.get(DigestSchedule, uuid.UUID(sid)).next_run_at = past
    db.commit()

    seen, sender = _capture()
    assert digest_service.run_due(db, sender=sender) == {"sent": 3, "failed": 0}
    assert digest_service.run_due(db, sender=sender) == {"sent": 0, "failed": 0}  # each slot once
    assert len(seen) == 2
    by_url = {str(r.url): json.loads(r.content) for r in seen}
    body = by_url[HOOK]
    assert body["sections"]["alerts"]["open"]["critical"] == 1
    assert body["sections"]["purchasing"]["late"] == [{"number": "PO-000007", "expected_date": "2020-01-01"}]
    assert body["link"].endswith("/digests")
    text = by_url[SLACK]["text"]
    assert "&lt;@here&gt;" in text and "<@here>" not in text  # data cannot ping a channel
    assert "1 late" in text and "1 drafts" in text

    db.expire_all()
    for sid in (hook["id"], slack["id"], in_app["id"]):
        s = db.get(DigestSchedule, uuid.UUID(sid))
        assert s.next_run_at > datetime.now(UTC) and s.last_run_at is not None
    assert db.get(DigestSchedule, uuid.UUID(later["id"])).last_run_at is None
    runs = client.get("/digests", headers=org.h("viewer")).json()
    assert runs["total"] == 3 and {r["status"] for r in runs["items"]} == {"delivered", "in_app"}


def test_failed_delivery_is_recorded(client, org, db):
    hook = _create(client, org, channel="webhook", target=HOOK).json()
    db.get(DigestSchedule, uuid.UUID(hook["id"])).next_run_at = datetime.now(UTC) - timedelta(seconds=5)
    db.commit()
    _, failing = _capture(status=500)
    assert digest_service.run_due(db, sender=failing) == {"sent": 0, "failed": 1}
    run = db.scalar(select(DigestRun))
    assert run.status == "failed" and "500" in run.error
    _, redirecting = _capture(status=302)
    s = db.get(DigestSchedule, run.schedule_id)
    assert digest_service.run(db, org.org_id, s, sender=redirecting).status == "failed"


def test_preview_and_reading_digests(client, org, other_org, db):
    _seed(db, org.org_id)
    p = Product(org_id=org.org_id, sku="SKU-9", name="Widget", on_hand=500, inventory_known=True, unit_cost=2.0)
    db.add(p)
    db.flush()
    start = date(2026, 6, 1)
    for i in range(60):
        db.add(SalesRecord(org_id=org.org_id, product_id=p.id, sale_date=start + timedelta(days=i), quantity=1))
    db.commit()
    r = client.post(
        "/digests/preview",
        json={"sections": ["stockouts", "overstock", "accuracy", "alerts", "purchasing"]},
        headers=org.h("viewer"),
    )
    assert r.status_code == 200, r.text
    out = r.json()
    sec = out["content"]["sections"]
    assert set(sec) == {"stockouts", "overstock", "accuracy", "alerts", "purchasing"}
    assert sec["stockouts"]["products"] == 1
    assert sec["overstock"]["overstock"] == 1 and sec["overstock"]["items"][0]["sku"] == "SKU-9"
    assert sec["accuracy"]["sufficient"] is False
    assert any("Forecast accuracy: not enough" in line for line in out["text"])

    s = _create(client, org).json()
    sent = client.post(f"/digests/schedules/{s['id']}/send", headers=org.h()).json()
    assert sent["status"] == "in_app" and sent["text"][0].startswith("Morning summary")
    assert client.get(f"/digests/{sent['id']}", headers=org.h("viewer")).json()["content"]["sections"]["alerts"]
    assert client.get(f"/digests/{sent['id']}", headers=other_org.h()).status_code == 404
    assert client.get("/digests", headers=other_org.h()).json()["total"] == 0


def test_inactive_schedules_are_skipped(client, org, db):
    s = _create(client, org).json()
    client.patch(f"/digests/schedules/{s['id']}", json={"expected_version": 1, "is_active": False}, headers=org.h())
    db.get(DigestSchedule, uuid.UUID(s["id"])).next_run_at = datetime.now(UTC) - timedelta(hours=1)
    db.commit()
    assert digest_service.run_due(db) == {"sent": 0, "failed": 0}
