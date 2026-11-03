"""API keys and outgoing webhooks (ADR-043)."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text

from app.core import outbound
from app.db.models import ApiKey, AuditEvent, Product, WebhookDelivery, WebhookEndpoint
from app.services import alert_service, api_keys, job_service, webhooks


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(outbound, "_resolve", lambda host: ["93.184.215.14"])


def _key(client, org, role="viewer", **kw):
    r = client.post("/api-keys", json={"name": "Sync", "role": role, **kw}, headers=org.h())
    assert r.status_code == 201, r.text
    return r.json()


def bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


# ---- API keys ------------------------------------------------------------------------------------------------------


def test_key_lifecycle_and_scope(client, org, other_org, db):
    made = _key(client, org, role="operator")
    secret = made["secret"]
    assert secret.startswith(made["key"]["prefix"] + "_") and len(secret) > 50
    row = db.scalar(select(ApiKey).where(ApiKey.prefix == made["key"]["prefix"]))
    assert row.key_hash != secret and secret not in json.dumps(made["key"])

    h = bearer(secret)
    assert client.get("/products", headers=h).status_code == 200
    assert client.post("/suppliers", json={"name": "Via key"}, headers=h).status_code == 201  # operator may
    assert client.post("/train", json={}, headers=h).status_code == 403  # analyst+ only
    # Keys never reach accounts, users, keys, settings or organisation deletion.
    for method, path in [
        ("get", "/auth/me"),
        ("get", "/auth/me/export"),
        ("post", "/auth/two-factor/setup"),
        ("get", "/users"),
        ("get", "/api-keys"),
        ("get", "/organisation/deletion"),
    ]:
        assert getattr(client, method)(path, headers=h).status_code in (401, 403), path
    # Tenant isolation: the key sees only its own organisation.
    other = client.post("/suppliers", json={"name": "Theirs"}, headers=other_org.h()).json()
    assert client.get(f"/suppliers/{other['id']}", headers=h).status_code == 404

    # Writes are audited in the creator's name with the key named.
    ev = db.scalars(
        select(AuditEvent).where(AuditEvent.action == "supplier.created", AuditEvent.org_id == org.org_id)
    ).one()
    assert ev.details["key_prefix"] == made["key"]["prefix"] and ev.actor_user_id == org.users["admin"]

    listed = client.get("/api-keys", headers=org.h()).json()
    assert listed[0]["last_used_at"] is not None and listed[0]["active"] is True
    assert client.delete(f"/api-keys/{made['key']['id']}", headers=other_org.h()).status_code == 404
    assert client.delete(f"/api-keys/{made['key']['id']}", headers=org.h()).status_code == 204
    assert client.get("/products", headers=h).status_code == 401
    assert client.delete(f"/api-keys/{made['key']['id']}", headers=org.h()).status_code == 409
    actions = {e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.org_id == org.org_id))}
    assert {"api_key.created", "api_key.revoked"} <= actions


def test_bad_expired_and_orphaned_keys_are_refused(client, org, db):
    made = _key(client, org)
    secret = made["secret"]
    for bad in (secret[:-1] + ("A" if secret[-1] != "A" else "B"), "aur_00000000_x", "aur_", "aur_x"):
        r = client.get("/products", headers=bearer(bad))
        assert r.status_code == 401 and r.json()["error"]["message"] == "Invalid or revoked API key"
    db.execute(text("UPDATE api_keys SET expires_at = now() - interval '1 minute'"))
    db.commit()
    assert client.get("/products", headers=bearer(secret)).status_code == 401

    second = _key(client, org)["secret"]
    assert client.get("/products", headers=bearer(second)).status_code == 200
    # Deactivating the creator stops their keys.
    db.execute(text("UPDATE users SET is_active = false WHERE id = :i"), {"i": org.users["admin"]})
    db.commit()
    assert client.get("/products", headers=bearer(second)).status_code == 401


def test_key_roles_are_capped(client, org, db):
    assert client.post("/api-keys", json={"name": "x", "role": "admin"}, headers=org.h()).status_code == 422
    assert client.post("/api-keys", json={"name": "x", "expires_in_days": 0}, headers=org.h()).status_code == 422
    assert client.post("/api-keys", json={"name": "x"}, headers=org.h("analyst")).status_code == 403  # admins only
    made = _key(client, org, role="analyst")
    # If the creator is later demoted, the key drops to the creator's role.
    db.execute(text("UPDATE users SET role = 'viewer' WHERE id = :i"), {"i": org.users["admin"]})
    db.commit()
    assert client.post("/suppliers", json={"name": "No"}, headers=bearer(made["secret"])).status_code == 403
    assert client.get("/products", headers=bearer(made["secret"])).status_code == 200


def test_active_key_limit(client, org, monkeypatch):
    monkeypatch.setattr(api_keys, "MAX_ACTIVE", 2)
    _key(client, org)
    k = _key(client, org)
    assert client.post("/api-keys", json={"name": "third"}, headers=org.h()).status_code == 409
    client.delete(f"/api-keys/{k['key']['id']}", headers=org.h())
    _key(client, org)


# ---- webhooks ------------------------------------------------------------------------------------------------------


class Receiver:
    def __init__(self, statuses: list[int] | None = None):
        self.statuses = statuses or []
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.statuses.pop(0) if self.statuses else 200, json={})

        return httpx.Client(transport=httpx.MockTransport(handler))


def _endpoint(client, org, events=("alert.opened",), url="https://hooks.example.com/aurora/s3cr3t-path"):
    r = client.post("/integrations/webhooks", json={"url": url, "events": list(events)}, headers=org.h())
    assert r.status_code == 201, r.text
    return r.json()


def test_endpoint_crud_and_secret_handling(client, org, other_org, db, monkeypatch):
    made = _endpoint(client, org)
    ep, secret = made["endpoint"], made["secret"]
    assert secret.startswith("whsec_") and ep["url_hint"] == "https://hooks.example.com/…"
    row = db.get(WebhookEndpoint, ep["id"])
    assert "s3cr3t-path" not in row.url_encrypted and secret not in row.secret_encrypted
    assert "s3cr3t-path" not in json.dumps(client.get("/integrations/webhooks", headers=org.h()).json())
    assert client.get("/integrations/webhooks", headers=other_org.h()).json() == []
    assert client.get("/integrations/webhooks", headers=org.h("analyst")).status_code == 403

    bad = client.post(
        "/integrations/webhooks", json={"url": "https://x.example.com/h", "events": ["nope"]}, headers=org.h()
    )
    assert bad.status_code == 422
    monkeypatch.setattr(outbound, "_resolve", lambda host: ["10.0.0.5"])
    private = client.post(
        "/integrations/webhooks", json={"url": "https://in.example.com/h", "events": ["alert.opened"]}, headers=org.h()
    )
    assert private.status_code == 422 and "private" in private.json()["error"]["message"]
    monkeypatch.setattr(outbound, "_resolve", lambda host: ["93.184.215.14"])

    r = client.patch(
        f"/integrations/webhooks/{ep['id']}", json={"events": ["job.finished", "model.promoted"]}, headers=org.h()
    )
    assert r.json()["events"] == ["job.finished", "model.promoted"]
    new = client.post(f"/integrations/webhooks/{ep['id']}/rotate-secret", headers=org.h()).json()["secret"]
    assert new != secret
    assert client.delete(f"/integrations/webhooks/{ep['id']}", headers=other_org.h()).status_code == 404
    assert client.delete(f"/integrations/webhooks/{ep['id']}", headers=org.h()).status_code == 204
    actions = {e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.org_id == org.org_id))}
    assert {"webhook.created", "webhook.updated", "webhook.secret_rotated", "webhook.deleted"} <= actions


def test_events_are_queued_with_the_change_and_signed_on_delivery(client, org, other_org, db):
    made = _endpoint(client, org, events=("alert.opened", "job.finished"))
    _endpoint(client, other_org)
    secret = made["secret"]

    alert_service.upsert(
        db, org_id=org.org_id, kind="stockout_risk", severity="high", title="Low", message="m", dedup_key="k1"
    )
    alert_service.upsert(  # same open alert again: not a new event
        db, org_id=org.org_id, kind="stockout_risk", severity="critical", title="Lower", message="m", dedup_key="k1"
    )
    db.commit()
    rows = db.scalars(select(WebhookDelivery)).all()
    assert len(rows) == 1 and rows[0].org_id == org.org_id and rows[0].event == "alert.opened"

    # A rolled-back change queues nothing.
    alert_service.upsert(db, org_id=org.org_id, kind="x", severity="low", title="t", message="m", dedup_key="k2")
    db.rollback()
    assert db.scalar(select(text("count(*)")).select_from(WebhookDelivery)) == 1

    rx = Receiver()
    out = webhooks.deliver_due(db, client=rx.client())
    assert out == {"sent": 1, "failed": 0, "skipped": 0}
    req = rx.requests[0]
    assert req.url == "https://hooks.example.com/aurora/s3cr3t-path"
    assert req.headers["Aurora-Event"] == "alert.opened"
    assert webhooks.verify(secret, req.headers["Aurora-Signature"], req.content)
    assert not webhooks.verify("whsec_wrong", req.headers["Aurora-Signature"], req.content)
    assert not webhooks.verify(secret, req.headers["Aurora-Signature"], req.content, now=int(time.time()) + 3600)
    body = json.loads(req.content)
    assert (
        body["type"] == "alert.opened"
        and body["data"]["severity"] == "high"
        and body["id"] == req.headers["Aurora-Delivery"]
    )
    assert webhooks.deliver_due(db, client=rx.client()) == {"sent": 0, "failed": 0, "skipped": 0}

    job, _ = job_service.create(db, org_id=org.org_id, kind="train", params={}, user_id=None)
    db.execute(text("UPDATE jobs SET status = 'running' WHERE id = :i"), {"i": job.id})
    db.commit()
    job_service.finish(db, job.id, "failed", error="boom")
    d = db.scalars(select(WebhookDelivery).where(WebhookDelivery.event == "job.finished")).one()
    assert d.payload["data"] == {"id": str(job.id), "kind": "train", "status": "failed", "error": "boom"}


def test_retries_back_off_then_fail_and_disable(client, org, db, monkeypatch):
    ep = _endpoint(client, org)["endpoint"]
    webhooks.emit(db, org.org_id, "alert.opened", {"id": "a1"})
    db.commit()
    d = db.scalars(select(WebhookDelivery)).one()
    rx = Receiver(statuses=[500] * 10)
    assert webhooks.deliver_due(db, client=rx.client())["failed"] == 1
    db.refresh(d)
    assert d.status == "pending" and d.attempts == 1 and d.last_status_code == 500
    gap = (d.next_attempt_at - datetime.now(UTC)).total_seconds()
    assert 50 < gap <= 60
    assert webhooks.deliver_due(db, client=rx.client())["failed"] == 0  # not due yet
    for _ in range(webhooks.MAX_ATTEMPTS - 1):
        db.execute(text("UPDATE webhook_deliveries SET next_attempt_at = now() - interval '1 second'"))
        db.commit()
        webhooks.deliver_due(db, client=rx.client())
    db.refresh(d)
    assert d.status == "failed" and d.attempts == webhooks.MAX_ATTEMPTS and len(rx.requests) == webhooks.MAX_ATTEMPTS

    # Retry by hand: one more attempt that succeeds.
    r = client.post(f"/integrations/webhooks/deliveries/{d.id}/retry", headers=org.h())
    assert r.status_code == 200 and r.json()["status"] == "pending"
    assert webhooks.deliver_due(db, client=Receiver().client())["sent"] == 1
    assert client.post(f"/integrations/webhooks/deliveries/{d.id}/retry", headers=org.h()).status_code == 409

    # Many failures in a row switch the endpoint off; switching it on again resets the count.
    monkeypatch.setattr(webhooks, "DISABLE_AFTER", 2)
    for _ in range(2):
        webhooks.emit(db, org.org_id, "alert.opened", {"id": "a2"})
    db.commit()
    webhooks.deliver_due(db, client=Receiver(statuses=[503, 503]).client())
    e = db.get(WebhookEndpoint, ep["id"])
    db.refresh(e)
    assert e.active is False and "failed attempts" in e.disabled_reason
    assert webhooks.emit(db, org.org_id, "alert.opened", {"id": "a3"}) == 0  # nothing queued while off
    r = client.patch(f"/integrations/webhooks/{ep['id']}", json={"active": True}, headers=org.h()).json()
    assert r["active"] and r["consecutive_failures"] == 0 and r["disabled_reason"] is None


def test_test_event_and_redirects(client, org, db, monkeypatch):
    ep = _endpoint(client, org)["endpoint"]
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

    real_client = httpx.Client
    monkeypatch.setattr(
        webhooks.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    r = client.post(f"/integrations/webhooks/{ep['id']}/test", headers=org.h())
    assert r.status_code == 200
    out = r.json()
    assert out["event"] == "ping" and out["status"] == "failed" and "Redirects" in out["last_error"]
    assert len(sent) == 1  # the redirect was not followed
    listed = client.get("/integrations/webhooks/deliveries", headers=org.h()).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == out["id"]


def test_old_deliveries_are_purged(client, org, db):
    _endpoint(client, org)
    webhooks.emit(db, org.org_id, "alert.opened", {"id": "old"})
    db.commit()
    webhooks.deliver_due(db, client=Receiver().client())
    assert webhooks.purge_old(db, now=datetime.now(UTC) + timedelta(days=31)) == 1


def test_exports_leave_out_key_and_webhook_secrets(client, org, db):
    from app.services import data_rights

    for model in data_rights.EXPORT_TABLES:
        for col in model.__table__.columns:  # type: ignore[attr-defined]
            name = col.name
            if any(s in name for s in ("hash", "secret", "encrypted", "_enc", "recovery_codes", "totp_pending")):
                assert name in data_rights.SECRET_COLUMNS, f"{model.__tablename__}.{name} would be exported"


def test_purchase_order_changes_queue_events(client, org, db):
    _endpoint(client, org, events=("purchase_order.status_changed",))
    p = Product(org_id=org.org_id, sku="W1", name="Widget", unit_cost=2.0)
    db.add(p)
    db.commit()
    sup = client.post("/suppliers", json={"name": "Acme"}, headers=org.h()).json()
    lines = [{"product_id": str(p.id), "quantity": 5}]
    po = client.post("/purchase-orders", json={"supplier_id": sup["id"], "lines": lines}, headers=org.h()).json()
    po = client.post(f"/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]}, headers=org.h())
    po = po.json()
    client.post(f"/purchase-orders/{po['id']}/send", json={"expected_version": po["version"]}, headers=org.h())
    rows = db.scalars(select(WebhookDelivery).order_by(WebhookDelivery.created_at)).all()
    assert [(r.payload["data"]["previous_status"], r.payload["data"]["status"]) for r in rows] == [
        ("draft", "approved"),
        ("approved", "sent"),
    ]
    assert rows[0].payload["data"]["number"] == po["number"]


def test_key_rotation_reencrypts_webhook_secrets(client, org, db, monkeypatch):
    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    from app.core import crypto
    from app.core.config import get_settings
    from app.services import maintenance

    s = get_settings()
    k_old, k_new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(s, "token_encryption_keys", SecretStr(k_old))
    made = _endpoint(client, org)
    monkeypatch.setattr(s, "token_encryption_keys", SecretStr(f"{k_new},{k_old}"))
    assert maintenance.reencrypt_secrets(db) == {"rotated": 2, "failed": 0}
    monkeypatch.setattr(s, "token_encryption_keys", SecretStr(k_new))
    db.expire_all()
    e = db.get(WebhookEndpoint, made["endpoint"]["id"])
    assert crypto.decrypt(e.secret_encrypted) == made["secret"]
    assert crypto.decrypt(e.url_encrypted) == "https://hooks.example.com/aurora/s3cr3t-path"
