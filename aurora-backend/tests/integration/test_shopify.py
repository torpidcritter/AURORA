"""Shopify: OAuth install (HMAC, state, replay), webhook verification, idempotency and tenant isolation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select

from app.core import crypto
from app.core.config import get_settings
from app.core.metrics import WEBHOOKS
from app.db.models import Product, SalesRecord, ShopifyOrder, ShopifyShop
from app.services import shopify_service

SECRET = "shpss_test_secret_not_real"
OLD_SECRET = "shpss_previous_secret_not_real"
SHOP = "demo-store.myshopify.com"


@pytest.fixture
def configured(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "shopify_api_key", "test-client-id")
    monkeypatch.setattr(s, "shopify_api_secret", SecretStr(SECRET))
    monkeypatch.setattr(s, "shopify_api_secret_previous", SecretStr(OLD_SECRET))
    monkeypatch.setattr(
        shopify_service,
        "_exchange_code",
        lambda shop, code, http: {"access_token": "shpat_live_token", "scope": "read_orders,read_products"},
    )
    return s


def sign_query(params: dict[str, str], secret: str = SECRET) -> dict[str, str]:
    msg = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return {**params, "hmac": hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()}


def start(client, org, shop=SHOP) -> str:
    r = client.post("/shopify/install", json={"shop": shop}, headers=org.h("admin"))
    assert r.status_code == 200, r.text
    url = urlparse(r.json()["authorize_url"])
    assert url.netloc == shop and url.path == "/admin/oauth/authorize"
    q = parse_qs(url.query)
    assert q["client_id"] == ["test-client-id"] and q["scope"] == ["read_orders,read_products"]
    return q["state"][0]


def callback(client, state, shop=SHOP, ts=None, secret=SECRET):
    params = sign_query({"code": "abc", "shop": shop, "state": state, "timestamp": str(ts or int(time.time()))}, secret)
    return client.get("/shopify/callback", params=params, follow_redirects=False)


def connect(client, org) -> None:
    r = callback(client, start(client, org))
    assert r.status_code == 303 and r.headers["location"].endswith("shopify=connected"), r.headers.get("location")


def webhook(client, payload, *, topic="orders/create", wid="wh-1", shop=SHOP, secret=SECRET, triggered=None, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    sig = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    headers = {
        "x-shopify-hmac-sha256": sig,
        "x-shopify-topic": topic,
        "x-shopify-shop-domain": shop,
        "x-shopify-webhook-id": wid,
        "x-shopify-triggered-at": (triggered or datetime.now(UTC)).isoformat(),
        "content-type": "application/json",
    }
    return client.post(f"/shopify/webhooks/{topic}", content=body, headers=headers)


ORDER = {
    "id": 1,
    "created_at": "2026-10-20T23:30:00-05:00",
    "line_items": [
        {"sku": "SHOP-A", "quantity": 2, "title": "Widget A"},
        {"sku": "SHOP-A", "quantity": 1, "title": "Widget A"},
        {"sku": "SHOP-B", "quantity": 4, "title": "Widget B"},
        {"sku": "", "quantity": 9, "title": "Gift card"},
    ],
}


def test_not_configured_returns_503(client, org):
    assert client.post("/shopify/install", json={"shop": SHOP}, headers=org.h()).status_code == 503


def test_install_requires_admin_and_valid_shop(client, org, configured):
    assert client.post("/shopify/install", json={"shop": SHOP}, headers=org.h("analyst")).status_code == 403
    for bad in ("evil.com", "x.myshopify.com.evil.com", "https://demo.myshopify.com", "a b.myshopify.com"):
        assert client.post("/shopify/install", json={"shop": bad}, headers=org.h()).status_code == 422, bad


def test_oauth_happy_path_stores_encrypted_token(client, org, configured, db):
    connect(client, org)
    shop = db.scalar(select(ShopifyShop).where(ShopifyShop.shop_domain == SHOP))
    assert shop is not None and shop.org_id == org.org_id
    assert "shpat_live_token" not in shop.access_token_enc
    assert crypto.decrypt(shop.access_token_enc) == "shpat_live_token"
    listed = client.get("/shopify/shops", headers=org.h()).json()
    assert listed["configured"] is True and listed["items"][0]["connected"] is True
    assert "access_token" not in json.dumps(listed)


@pytest.mark.parametrize(
    "mutate",
    ["bad_hmac", "stale", "replay", "shop_mismatch", "unknown_state"],
)
def test_oauth_callback_rejections(client, org, configured, mutate):
    state = start(client, org)
    if mutate == "bad_hmac":
        r = callback(client, state, secret="wrong")
    elif mutate == "stale":
        r = callback(client, state, ts=int(time.time()) - 7200)
    elif mutate == "replay":
        assert callback(client, state).headers["location"].endswith("connected")
        r = callback(client, state)
    elif mutate == "shop_mismatch":
        r = callback(client, state, shop="other-store.myshopify.com")
    else:
        r = callback(client, "not-a-real-state")
    assert r.status_code == 303
    assert "shopify=error" in r.headers["location"]
    assert r.headers["location"].startswith(get_settings().frontend_url)


def test_shop_cannot_be_claimed_by_second_org(client, org, other_org, configured):
    connect(client, org)
    assert client.post("/shopify/install", json={"shop": SHOP}, headers=other_org.h()).status_code == 409


def _webhook_count(outcome: str) -> float:
    return WEBHOOKS.labels("shopify", outcome)._value.get()


def test_order_webhook_ingests_once_per_delivery(client, org, other_org, configured, db):
    connect(client, org)
    processed, dupes = _webhook_count("processed"), _webhook_count("duplicate")
    r = webhook(client, ORDER)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "duplicate": False, "lines": 2, "skipped_lines": 1, "date": "2026-10-20"}
    # Redelivery with the same webhook id is acknowledged but not applied again.
    assert webhook(client, ORDER).json() == {"ok": True, "duplicate": True}
    assert (_webhook_count("processed") - processed, _webhook_count("duplicate") - dupes) == (1, 1)
    # A different delivery on the same day adds to the daily total.
    webhook(client, {**ORDER, "id": 2, "line_items": [{"sku": "SHOP-A", "quantity": 5}]}, wid="wh-2")
    rows = {
        sku: (qty, src)
        for sku, qty, src in db.execute(
            select(Product.sku, SalesRecord.quantity, SalesRecord.source).join(
                Product, Product.id == SalesRecord.product_id
            )
        ).all()
    }
    assert rows == {"SHOP-A": (8.0, "shopify"), "SHOP-B": (4.0, "shopify")}
    prods = db.scalars(select(Product).where(Product.sku.in_(["SHOP-A", "SHOP-B"]))).all()
    assert all(p.org_id == org.org_id and p.inventory_known is False for p in prods)
    assert client.get("/products?q=SHOP", headers=other_org.h()).json()["total"] == 0


def test_webhook_signature_and_freshness(client, org, configured, db):
    connect(client, org)
    bad = _webhook_count("bad_signature")
    assert webhook(client, ORDER, secret="wrong").status_code == 401
    assert _webhook_count("bad_signature") - bad == 1
    # A retry within Shopify's ~4 hour retry schedule still carries the original trigger time and is accepted.
    assert webhook(client, ORDER, wid="retry", triggered=datetime.now(UTC) - timedelta(hours=4)).status_code == 200
    assert webhook(client, ORDER, wid="old", triggered=datetime.now(UTC) - timedelta(hours=13)).status_code == 403
    # Tampering with the body after signing fails verification.
    body = json.dumps(ORDER).encode()
    sig = base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()
    r = client.post(
        "/shopify/webhooks/orders/create",
        content=body.replace(b"2,", b"200,"),
        headers={"x-shopify-hmac-sha256": sig, "x-shopify-shop-domain": SHOP, "x-shopify-webhook-id": "t"},
    )
    assert r.status_code == 401
    # The previous secret is still accepted during a rotation.
    assert webhook(client, ORDER, secret=OLD_SECRET, wid="rot").status_code == 200
    assert db.scalar(select(SalesRecord.id).limit(1)) is not None


def test_failed_processing_rolls_back_so_retry_applies(client, org, configured, db, monkeypatch):
    connect(client, org)
    real = shopify_service._ingest_order

    def boom(*a, **k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(shopify_service, "_ingest_order", boom)
    with pytest.raises(RuntimeError):
        webhook(client, ORDER, wid="retry-me")
    monkeypatch.setattr(shopify_service, "_ingest_order", real)
    r = webhook(client, ORDER, wid="retry-me")
    # The ledger row was rolled back with everything else, so the retry is applied in full.
    assert r.json()["duplicate"] is False and r.json()["lines"] == 2


def test_test_orders_unknown_shops_and_uninstall(client, org, configured, db):
    assert webhook(client, ORDER, wid="nobody").json()["ignored"] == "shop not connected"
    connect(client, org)
    assert webhook(client, {**ORDER, "test": True}, wid="t1").json()["skipped"] == "test order"
    assert webhook(client, {}, topic="app/uninstalled", wid="u1").json()["uninstalled"] is True
    shop = db.scalar(select(ShopifyShop).where(ShopifyShop.shop_domain == SHOP))
    db.refresh(shop)
    assert shop.access_token_enc == "" and shop.uninstalled_at is not None
    assert webhook(client, ORDER, wid="after").json()["ignored"] == "shop disconnected"
    assert webhook(client, {}, topic="customers/redact", wid="g1").status_code == 200
    assert webhook(client, {}, topic="products/delete", wid="x1").status_code == 404
    assert db.scalar(select(SalesRecord.id).limit(1)) is None


def test_shop_redact_erases_order_level_data(client, org, other_org, configured, db):
    connect(client, org)
    assert webhook(client, ORDER, wid="o1").json()["lines"] == 2
    other = ShopifyOrder(
        org_id=other_org.org_id, shop_domain="other.myshopify.com", order_id="9", order_date=date(2026, 5, 1), lines={}
    )
    db.add(other)
    db.commit()
    r = webhook(client, {"shop_domain": SHOP}, topic="shop/redact", wid="r1").json()
    assert r["uninstalled"] is True and r["orders_erased"] == 1
    remaining = db.scalars(select(ShopifyOrder.shop_domain)).all()
    assert remaining == ["other.myshopify.com"]


def test_disconnect_is_tenant_scoped(client, org, other_org, configured, db):
    connect(client, org)
    sid = client.get("/shopify/shops", headers=org.h()).json()["items"][0]["id"]
    assert client.delete(f"/shopify/shops/{sid}", headers=other_org.h()).status_code == 404
    assert client.delete(f"/shopify/shops/{sid}", headers=org.h("operator")).status_code == 403
    assert client.delete(f"/shopify/shops/{sid}", headers=org.h()).status_code == 204
    assert client.get("/shopify/shops", headers=org.h()).json()["items"][0]["connected"] is False


def test_code_exchange_uses_https_and_checks_response(configured, monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"access_token": "tok", "scope": "read_orders"})

    monkeypatch.undo()  # restore the real _exchange_code
    s = get_settings()
    monkeypatch.setattr(s, "shopify_api_key", "cid")
    monkeypatch.setattr(s, "shopify_api_secret", SecretStr(SECRET))
    out = shopify_service._exchange_code(SHOP, "code1", httpx.Client(transport=httpx.MockTransport(handler)))
    assert out["access_token"] == "tok"
    assert seen["url"] == f"https://{SHOP}/admin/oauth/access_token"
    assert seen["body"] == {"client_id": "cid", "client_secret": SECRET, "code": "code1"}
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={})))
    with pytest.raises(Exception, match="rejected"):
        shopify_service._exchange_code(SHOP, "c", bad)


def test_token_key_rotation(monkeypatch):
    s = get_settings()
    k_old, k_new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(s, "token_encryption_keys", SecretStr(k_old))
    enc = crypto.encrypt("secret-token")
    monkeypatch.setattr(s, "token_encryption_keys", SecretStr(f"{k_new},{k_old}"))
    assert crypto.decrypt(enc) == "secret-token"
    rotated = crypto.rotate(enc)
    monkeypatch.setattr(s, "token_encryption_keys", SecretStr(k_new))
    assert crypto.decrypt(rotated) == "secret-token"
    with pytest.raises(ValueError):
        crypto.decrypt(enc)


def test_reencrypt_tokens_moves_every_shop_to_the_newest_key(org, db, monkeypatch):
    s = get_settings()
    k_old, k_new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(s, "token_encryption_keys", SecretStr(k_old))
    db.add_all(
        [
            ShopifyShop(org_id=org.org_id, shop_domain="one.myshopify.com", access_token_enc=crypto.encrypt("tok-1")),
            ShopifyShop(org_id=org.org_id, shop_domain="two.myshopify.com", access_token_enc=crypto.encrypt("tok-2")),
            ShopifyShop(org_id=org.org_id, shop_domain="lost.myshopify.com", access_token_enc="not-a-fernet-token"),
        ]
    )
    db.commit()
    monkeypatch.setattr(s, "token_encryption_keys", SecretStr(f"{k_new},{k_old}"))

    assert shopify_service.reencrypt_tokens(db) == {"rotated": 2, "failed": ["lost.myshopify.com"]}

    # With the old key removed, the rotated tokens still decrypt.
    monkeypatch.setattr(s, "token_encryption_keys", SecretStr(k_new))
    db.expire_all()
    tokens = {sh.shop_domain: sh.access_token_enc for sh in db.scalars(select(ShopifyShop))}
    assert crypto.decrypt(tokens["one.myshopify.com"]) == "tok-1"
    assert crypto.decrypt(tokens["two.myshopify.com"]) == "tok-2"
    assert tokens["lost.myshopify.com"] == "not-a-fernet-token"


def _sales(db, org_id) -> dict[tuple[str, str], float]:
    db.expire_all()
    rows = db.execute(
        select(Product.sku, SalesRecord.source, SalesRecord.quantity)
        .join(Product, Product.id == SalesRecord.product_id)
        .where(SalesRecord.org_id == org_id)
    ).all()
    return {(sku, src): q for sku, src, q in rows}


def _refund(rid, *lines):
    return {
        "id": rid,
        "order_id": 1,
        "refund_line_items": [{"quantity": q, "restock_type": rt, "line_item": {"sku": sku}} for sku, q, rt in lines],
    }


def test_same_order_under_a_new_webhook_id_is_applied_once(client, org, configured, db):
    connect(client, org)
    webhook(client, ORDER, wid="a")
    assert webhook(client, ORDER, wid="b").json() == {"ok": True, "duplicate": False, "duplicate_order": True}
    assert _sales(db, org.org_id) == {("SHOP-A", "shopify"): 3.0, ("SHOP-B", "shopify"): 4.0}


def test_refunds_remove_only_restocked_units_and_cancellation_removes_the_rest(client, org, configured, db):
    connect(client, org)
    webhook(client, ORDER, wid="create")
    r = webhook(
        client,
        _refund(10, ("SHOP-A", 1, "return"), ("SHOP-B", 2, "no_restock")),
        topic="refunds/create",
        wid="refund-10",
    )
    assert r.status_code == 200, r.text
    assert r.json()["units_removed"] == 1 and r.json()["units_kept_as_sales"] == 2
    assert _sales(db, org.org_id) == {("SHOP-A", "shopify"): 2.0, ("SHOP-B", "shopify"): 4.0}
    # The same refund delivered again under another webhook id changes nothing.
    again = webhook(client, _refund(10, ("SHOP-A", 1, "return")), topic="refunds/create", wid="refund-10-again")
    assert again.json()["ignored"] == "refund already applied"

    r = webhook(client, {**ORDER, "cancelled_at": "2026-10-21T09:00:00Z"}, topic="orders/cancelled", wid="cancel")
    assert r.json()["cancelled"] is True and r.json()["units_removed"] == 6
    assert _sales(db, org.org_id) == {("SHOP-A", "shopify"): 0.0, ("SHOP-B", "shopify"): 0.0}
    # Shopify also sends a refund with restock_type "cancel" for a cancelled order: nothing is left to remove.
    r = webhook(client, _refund(11, ("SHOP-B", 4, "cancel")), topic="refunds/create", wid="refund-11")
    assert r.json()["units_removed"] == 0
    assert webhook(client, ORDER, topic="orders/cancelled", wid="cancel-2").json()["ignored"] == "already cancelled"
    assert _sales(db, org.org_id) == {("SHOP-A", "shopify"): 0.0, ("SHOP-B", "shopify"): 0.0}


def test_refund_after_cancellation_and_unknown_orders(client, org, configured, db):
    connect(client, org)
    webhook(client, ORDER, wid="create")
    webhook(client, ORDER, topic="orders/cancelled", wid="cancel")
    r = webhook(client, _refund(12, ("SHOP-A", 3, "cancel")), topic="refunds/create", wid="refund-12")
    assert r.json()["units_removed"] == 0
    unknown = {**_refund(13, ("SHOP-A", 1, "return")), "order_id": 999}
    assert webhook(client, unknown, topic="refunds/create", wid="r13").json()["ignored"] == "order not recorded"
    assert webhook(client, {**ORDER, "id": 998}, topic="orders/cancelled", wid="c2").json()["ignored"] == (
        "order not recorded"
    )


def test_cancellation_touches_only_that_orders_units(client, org, other_org, configured, db):
    connect(client, org)
    webhook(client, ORDER, wid="o1")
    webhook(client, {**ORDER, "id": 2, "line_items": [{"sku": "SHOP-A", "quantity": 5}]}, wid="o2")
    day = datetime(2026, 10, 20).date()
    pid = db.scalar(select(Product.id).where(Product.org_id == org.org_id, Product.sku == "SHOP-A"))
    # Uploaded sales for the same product and day, and another tenant's product with the same SKU.
    other = Product(org_id=other_org.org_id, sku="SHOP-A", name="Other A")
    db.add(other)
    db.flush()
    db.add_all(
        [
            SalesRecord(org_id=org.org_id, product_id=pid, sale_date=day, quantity=7, source="upload"),
            SalesRecord(org_id=other_org.org_id, product_id=other.id, sale_date=day, quantity=11, source="shopify"),
        ]
    )
    db.commit()

    webhook(client, ORDER, topic="orders/cancelled", wid="cancel-o1")

    assert _sales(db, org.org_id) == {
        ("SHOP-A", "shopify"): 5.0,
        ("SHOP-B", "shopify"): 0.0,
        ("SHOP-A", "upload"): 7.0,
    }
    assert _sales(db, other_org.org_id) == {("SHOP-A", "shopify"): 11.0}


def _graphql_transport(seen, *, fail_topic=None, taken_topic=None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https" and request.url.path == "/admin/api/2025-07/graphql.json"
        assert request.headers["X-Shopify-Access-Token"] == "shpat_live_token"
        body = json.loads(request.content)
        topic = body["variables"]["topic"]
        seen.append((request.url.host, topic, body["variables"]["sub"]["callbackUrl"]))
        errors = []
        if topic == fail_topic:
            errors = [{"field": ["callbackUrl"], "message": "Address is invalid"}]
        if topic == taken_topic:
            errors = [{"field": ["callbackUrl"], "message": "Address for this topic has already been taken"}]
        return httpx.Response(
            200, json={"data": {"webhookSubscriptionCreate": {"webhookSubscription": None, "userErrors": errors}}}
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_install_registers_webhooks_over_https(client, org, configured, db, monkeypatch):
    monkeypatch.setattr(configured, "shopify_webhook_base_url", "https://api.example.com")
    seen = []
    real = shopify_service.register_webhooks
    monkeypatch.setattr(
        shopify_service,
        "register_webhooks",
        lambda shop, token, http=None: real(shop, token, _graphql_transport(seen, taken_topic="APP_UNINSTALLED")),
    )
    connect(client, org)
    assert sorted(t for _, t, _ in seen) == ["APP_UNINSTALLED", "ORDERS_CANCELLED", "ORDERS_CREATE", "REFUNDS_CREATE"]
    assert all(host == SHOP for host, _, _ in seen)
    assert ("https://api.example.com/shopify/webhooks/orders/create") in {u for _, _, u in seen}
    shops = client.get("/shopify/shops", headers=org.h("admin")).json()["items"]
    # "already been taken" means the subscription exists: counted as registered.
    assert shops[0]["webhooks_ok"] is True and set(shops[0]["webhooks"].values()) == {"registered"}


def test_webhook_registration_errors_are_reported_and_can_be_retried(client, org, configured, db, monkeypatch):
    connect(client, org)  # test settings use an http:// redirect URL, so registration is skipped
    shop = client.get("/shopify/shops", headers=org.h("admin")).json()["items"][0]
    assert shop["webhooks_ok"] is False
    assert all(v.startswith("skipped: Shopify requires an HTTPS") for v in shop["webhooks"].values())

    monkeypatch.setattr(configured, "shopify_webhook_base_url", "https://api.example.com")
    seen = []
    real = shopify_service.register_webhooks
    monkeypatch.setattr(
        shopify_service,
        "register_webhooks",
        lambda s, t, http=None: real(s, t, _graphql_transport(seen, fail_topic="REFUNDS_CREATE")),
    )
    assert client.post(f"/shopify/shops/{shop['id']}/webhooks", headers=org.h("analyst")).status_code == 403
    r = client.post(f"/shopify/shops/{shop['id']}/webhooks", headers=org.h("admin"))
    assert r.status_code == 200, r.text
    assert r.json()["webhooks"]["refunds/create"] == "error: Address is invalid"
    assert r.json()["webhooks"]["orders/create"] == "registered"
    assert client.get("/shopify/shops", headers=org.h("admin")).json()["items"][0]["webhooks_ok"] is False


def test_refresh_webhooks_is_tenant_scoped(client, org, other_org, configured, db):
    connect(client, org)
    shop_id = client.get("/shopify/shops", headers=org.h("admin")).json()["items"][0]["id"]
    assert client.post(f"/shopify/shops/{shop_id}/webhooks", headers=other_org.h("admin")).status_code == 404


def test_failed_signed_deliveries_are_recorded_for_the_shop_owner(client, org, configured, db):
    from app.db.models import AuditEvent

    connect(client, org)
    stale = datetime.now(UTC) - timedelta(hours=13)
    assert webhook(client, ORDER, wid="late-1", triggered=stale).status_code == 403
    assert webhook(client, ORDER, wid="forged", secret="wrong").status_code == 401  # unsigned: metrics only
    rows = db.scalars(select(AuditEvent).where(AuditEvent.action == "shopify.webhook_failed")).all()
    assert len(rows) == 1
    ev = rows[0]
    assert ev.org_id == org.org_id and ev.outcome == "failure" and ev.resource_id == "late-1"
    assert ev.details["topic"] == "orders/create" and ev.details["error"] == "forbidden"
    # The failed delivery left no webhook row behind, so a later retry with the same id is applied.
    assert webhook(client, ORDER, wid="late-1").json()["duplicate"] is False
    listed = client.get("/audit?action=shopify.webhook_failed", headers=org.h()).json()
    assert listed["total"] == 1
