"""Shopify integration: OAuth install, HMAC-verified webhooks, idempotent order ingestion.

Flow
1. An admin calls `start_install(shop)`; we store a single-use, 10-minute `state` bound to their organisation.
2. Shopify redirects the browser to `/shopify/callback`. We verify the query HMAC, timestamp, shop domain and
   state (consumed atomically), exchange the code for an offline token and store it encrypted.
3. Shopify sends webhooks. We verify the body HMAC (current or previous secret, constant-time), reject stale
   deliveries, and record the webhook id in `webhook_events` in the same transaction as the sales update, so a
   redelivery is acknowledged without being applied twice.

Order line items with a SKU become daily `sales_records` rows with `source="shopify"`, dated by the order's local
calendar day. A ledger (`shopify_orders`) records what each order added, so that:
- `orders/create` delivered twice under different webhook ids is still applied once;
- `orders/cancelled` removes whatever the order still contributes;
- `refunds/create` removes only units that went back into stock (restock type return, cancel or legacy_restock);
  a refund without a return keeps the sale, because the units did leave inventory;
- removals never exceed what the order added, whatever order the webhooks arrive in.
Order edits (`orders/updated`) are not reconciled (documented limitation).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import crypto
from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden, NotFound, Unavailable, ValidationFailed
from app.db.models import OAuthState, Product, SalesRecord, ShopifyOrder, ShopifyShop, WebhookEvent

SHOP_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com$")
STATE_TTL = timedelta(minutes=10)
SOURCE = "shopify"
SUPPORTED_TOPICS = {
    "orders/create",
    "orders/cancelled",
    "refunds/create",
    "app/uninstalled",
    "customers/data_request",
    "customers/redact",
    "shop/redact",
}
# Topics Aurora subscribes to after install, with their Admin GraphQL enum names. The GDPR topics
# (customers/data_request, customers/redact, shop/redact) are mandatory compliance webhooks that Shopify configures
# from the app's settings, not through this API.
WEBHOOK_TOPICS = {
    "orders/create": "ORDERS_CREATE",
    "orders/cancelled": "ORDERS_CANCELLED",
    "refunds/create": "REFUNDS_CREATE",
    "app/uninstalled": "APP_UNINSTALLED",
}
_SUBSCRIBE = """
mutation Subscribe($topic: WebhookSubscriptionTopic!, $sub: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $sub) {
    webhookSubscription { id }
    userErrors { field message }
  }
}
"""
# Restock types meaning the units came back into inventory, so they are no longer demand.
RESTOCKED = {"return", "cancel", "legacy_restock"}


def _require_config() -> tuple[str, str]:
    s = get_settings()
    if not s.shopify_api_key or s.shopify_api_secret is None:
        raise Unavailable("The Shopify integration is not configured on this server")
    return s.shopify_api_key, s.shopify_api_secret.get_secret_value()


def _secrets() -> list[bytes]:
    s = get_settings()
    out = [x.get_secret_value().encode() for x in (s.shopify_api_secret, s.shopify_api_secret_previous) if x]
    if not out:
        raise Unavailable("The Shopify integration is not configured on this server")
    return out


def normalise_shop(shop: str) -> str:
    shop = (shop or "").strip().lower()
    if not SHOP_RE.fullmatch(shop):
        raise ValidationFailed("Shop must look like your-store.myshopify.com")
    return shop


# --- OAuth -----------------------------------------------------------------------------------------------------


def start_install(db: Session, org_id: uuid.UUID, user_id: uuid.UUID, shop: str) -> str:
    api_key, _ = _require_config()
    shop = normalise_shop(shop)
    existing = db.scalar(select(ShopifyShop).where(ShopifyShop.shop_domain == shop))
    if existing is not None and existing.org_id != org_id and existing.uninstalled_at is None:
        raise Conflict("This shop is already connected to another Aurora organisation")
    state = secrets.token_urlsafe(32)
    db.add(
        OAuthState(
            state=state, org_id=org_id, user_id=user_id, shop_domain=shop, expires_at=datetime.now(UTC) + STATE_TTL
        )
    )
    s = get_settings()
    query = urlencode(
        {"client_id": api_key, "scope": s.shopify_scopes, "redirect_uri": s.shopify_redirect_url, "state": state}
    )
    return f"https://{shop}/admin/oauth/authorize?{query}"


def verify_query_hmac(params: dict[str, str]) -> bool:
    """Shopify signs OAuth redirects: HMAC-SHA256 (hex) over the sorted query string without `hmac`."""
    given = params.get("hmac", "")
    msg = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k not in ("hmac", "signature")).encode()
    return any(hmac.compare_digest(hmac.new(k, msg, hashlib.sha256).hexdigest(), given) for k in _secrets())


def _exchange_code(shop: str, code: str, http: httpx.Client | None) -> dict[str, Any]:
    api_key, secret = _require_config()
    client = http or httpx.Client(timeout=10.0)
    try:
        r = client.post(
            f"https://{shop}/admin/oauth/access_token",
            json={"client_id": api_key, "client_secret": secret, "code": code},
        )
    except httpx.HTTPError as exc:
        raise Unavailable("Could not reach Shopify to complete the installation") from exc
    finally:
        if http is None:
            client.close()
    if r.status_code != 200:
        raise ValidationFailed("Shopify rejected the authorisation code")
    data = r.json()
    if not isinstance(data.get("access_token"), str):
        raise ValidationFailed("Shopify returned no access token")
    return data


def webhook_base_url() -> str:
    s = get_settings()
    if s.shopify_webhook_base_url:
        return s.shopify_webhook_base_url.rstrip("/")
    u = urlparse(s.shopify_redirect_url)
    return f"{u.scheme}://{u.netloc}"


def register_webhooks(shop: str, access_token: str, http: httpx.Client | None = None) -> dict[str, str]:
    """Subscribe the shop to WEBHOOK_TOPICS. Returns a status per topic: "registered", "skipped: ..." or
    "error: ...". An existing subscription for the same address counts as registered, so this is safe to repeat."""
    base = webhook_base_url()
    if not base.startswith("https://"):
        return {
            t: "skipped: Shopify requires an HTTPS webhook address (set SHOPIFY_WEBHOOK_BASE_URL)"
            for t in WEBHOOK_TOPICS
        }
    version = get_settings().shopify_api_version
    client = http or httpx.Client(timeout=10.0)
    out: dict[str, str] = {}
    try:
        for topic, enum in WEBHOOK_TOPICS.items():
            variables = {"topic": enum, "sub": {"callbackUrl": f"{base}/shopify/webhooks/{topic}", "format": "JSON"}}
            try:
                r = client.post(
                    f"https://{shop}/admin/api/{version}/graphql.json",
                    headers={"X-Shopify-Access-Token": access_token},
                    json={"query": _SUBSCRIBE, "variables": variables},
                )
            except httpx.HTTPError:
                out[topic] = "error: Shopify unreachable"
                continue
            if r.status_code != 200:
                out[topic] = f"error: HTTP {r.status_code}"
                continue
            try:
                body = r.json()
            except ValueError:
                out[topic] = "error: invalid response"
                continue
            payload = (body.get("data") or {}).get("webhookSubscriptionCreate") or {}
            errors = [str(e.get("message", ""))[:200] for e in payload.get("userErrors") or []]
            if body.get("errors"):
                out[topic] = "error: " + str(body["errors"][0].get("message", "GraphQL error"))[:200]
            elif not errors:
                out[topic] = "registered"
            elif any("already been taken" in e for e in errors):
                out[topic] = "registered"
            else:
                out[topic] = "error: " + "; ".join(errors)
    finally:
        if http is None:
            client.close()
    return out


def refresh_webhooks(
    db: Session, org_id: uuid.UUID, shop_id: uuid.UUID, http: httpx.Client | None = None
) -> ShopifyShop:
    """Re-register webhooks for a connected shop (e.g. after moving the API to a new address)."""
    _require_config()
    shop = db.get(ShopifyShop, shop_id)
    if shop is None or shop.org_id != org_id:
        raise NotFound("Shop not found")
    if shop.uninstalled_at is not None:
        raise Conflict("Reconnect the shop before registering webhooks")
    try:
        token = crypto.decrypt(shop.access_token_enc)
    except ValueError as exc:
        raise Conflict("The stored access token cannot be decrypted; reconnect the shop") from exc
    shop.webhooks = register_webhooks(shop.shop_domain, token, http)
    shop.webhooks_checked_at = datetime.now(UTC)
    db.flush()
    return shop


def complete_install(db: Session, params: dict[str, str], http: httpx.Client | None = None) -> ShopifyShop:
    s = get_settings()
    _require_config()
    if not verify_query_hmac(params):
        raise Forbidden("Invalid Shopify signature")
    try:
        ts = int(params.get("timestamp", "0"))
    except ValueError as exc:
        raise Forbidden("Invalid timestamp") from exc
    if abs(datetime.now(UTC).timestamp() - ts) > s.shopify_oauth_max_age_s:
        raise Forbidden("Shopify redirect is too old")
    shop = normalise_shop(params.get("shop", ""))
    code = params.get("code") or ""
    if not code:
        raise ValidationFailed("Missing authorisation code")
    now = datetime.now(UTC)
    # Consume the state atomically so a replayed callback cannot be used twice.
    row = db.execute(
        update(OAuthState)
        .where(
            OAuthState.state == params.get("state", ""),
            OAuthState.consumed_at.is_(None),
            OAuthState.expires_at > now,
            OAuthState.shop_domain == shop,
        )
        .values(consumed_at=now)
        .returning(OAuthState.org_id)
    ).first()
    if row is None:
        raise Forbidden("Unknown, expired or already used install request")
    org_id = row[0]
    token = _exchange_code(shop, code, http)
    granted = set(filter(None, (token.get("scope") or "").split(",")))
    missing = set(s.shopify_scopes.split(",")) - granted
    if missing:
        raise ValidationFailed(f"Shopify did not grant required scopes: {', '.join(sorted(missing))}")
    existing = db.scalar(select(ShopifyShop).where(ShopifyShop.shop_domain == shop).with_for_update())
    if existing is not None and existing.org_id != org_id and existing.uninstalled_at is None:
        raise Conflict("This shop is already connected to another Aurora organisation")
    if existing is None:
        existing = ShopifyShop(org_id=org_id, shop_domain=shop, access_token_enc="")
        db.add(existing)
    existing.org_id = org_id
    existing.access_token_enc = crypto.encrypt(token["access_token"])
    existing.scopes = ",".join(sorted(granted))
    existing.uninstalled_at = None
    # Without subscriptions Shopify sends nothing. A failure here does not undo the install: the status is shown in
    # the UI and an admin can retry with POST /shopify/shops/{id}/webhooks.
    existing.webhooks = register_webhooks(shop, token["access_token"], http)
    existing.webhooks_checked_at = datetime.now(UTC)
    db.flush()
    return existing


def list_shops(db: Session, org_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.scalars(select(ShopifyShop).where(ShopifyShop.org_id == org_id).order_by(ShopifyShop.shop_domain)).all()
    return [
        {
            "id": str(r.id),
            "shop_domain": r.shop_domain,
            "scopes": r.scopes.split(",") if r.scopes else [],
            "connected": r.uninstalled_at is None,
            "connected_at": r.created_at.isoformat(),
            "uninstalled_at": r.uninstalled_at.isoformat() if r.uninstalled_at else None,
            "webhooks": r.webhooks or {},
            "webhooks_ok": bool(r.webhooks) and all(v == "registered" for v in r.webhooks.values()),
            "webhooks_checked_at": r.webhooks_checked_at.isoformat() if r.webhooks_checked_at else None,
        }
        for r in rows
    ]


def disconnect(db: Session, org_id: uuid.UUID, shop_id: uuid.UUID) -> ShopifyShop:
    shop = db.get(ShopifyShop, shop_id)
    if shop is None or shop.org_id != org_id:
        raise NotFound("Shop not found")
    _mark_uninstalled(shop)
    return shop


def _mark_uninstalled(shop: ShopifyShop) -> None:
    shop.uninstalled_at = shop.uninstalled_at or datetime.now(UTC)
    shop.access_token_enc = ""  # drop the credential; sales history is kept


# --- webhooks --------------------------------------------------------------------------------------------------


def verify_webhook_hmac(body: bytes, header: str | None) -> bool:
    if not header:
        return False
    return any(
        hmac.compare_digest(base64.b64encode(hmac.new(k, body, hashlib.sha256).digest()).decode(), header)
        for k in _secrets()
    )


def _order_date(order: dict[str, Any]) -> date:
    raw = order.get("created_at") or order.get("processed_at")
    if not isinstance(raw, str):
        raise ValueError("order has no created_at")
    # Shopify timestamps carry the shop's UTC offset; the local calendar day is the sales day.
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()


def _sku(li: dict[str, Any]) -> str:
    return str(li.get("sku") or "").strip()[:120]


def _ingest_order(db: Session, org_id: uuid.UUID, shop_domain: str, order: dict[str, Any]) -> dict[str, Any]:
    if order.get("test") is True:
        return {"skipped": "test order"}
    day = _order_date(order)
    qty_by_sku: dict[str, float] = {}
    names: dict[str, str] = {}
    skipped = 0
    for li in order.get("line_items") or []:
        sku = _sku(li)
        q = li.get("quantity")
        if not sku or not isinstance(q, int | float) or isinstance(q, bool) or q <= 0:
            skipped += 1
            continue
        qty_by_sku[sku] = qty_by_sku.get(sku, 0.0) + float(q)
        names.setdefault(sku, str(li.get("title") or sku)[:255])
    if not qty_by_sku:
        return {"lines": 0, "skipped_lines": skipped}
    order_id = str(order.get("id") or "").strip()[:40]
    if order_id:
        recorded = db.execute(
            insert(ShopifyOrder)
            .values(org_id=org_id, shop_domain=shop_domain, order_id=order_id, order_date=day, lines=qty_by_sku)
            .on_conflict_do_nothing(constraint="uq_shopify_orders_shop_order")
            .returning(ShopifyOrder.id)
        ).scalar()
        if recorded is None:
            return {"duplicate_order": True}
    # New SKUs are created with unknown stock, so they never get a recommendation until someone counts them.
    stmt = insert(Product).values(
        [
            {"id": uuid.uuid4(), "org_id": org_id, "sku": k, "name": names[k], "inventory_known": False}
            for k in qty_by_sku
        ]
    )
    db.execute(stmt.on_conflict_do_nothing(constraint="uq_products_org_sku"))
    ids = dict(
        db.execute(select(Product.sku, Product.id).where(Product.org_id == org_id, Product.sku.in_(list(qty_by_sku))))
        .tuples()
        .all()
    )
    rows = [
        {"org_id": org_id, "product_id": ids[k], "sale_date": day, "quantity": q, "source": SOURCE}
        for k, q in qty_by_sku.items()
    ]
    ins = insert(SalesRecord).values(rows)
    db.execute(
        ins.on_conflict_do_update(
            constraint="uq_sales_product_date_source",
            set_={"quantity": SalesRecord.quantity + ins.excluded.quantity},
        )
    )
    return {"lines": len(rows), "skipped_lines": skipped, "date": day.isoformat()}


def _ledger(db: Session, shop_domain: str, order_id: Any) -> ShopifyOrder | None:
    oid = str(order_id or "").strip()[:40]
    if not oid:
        return None
    return db.scalar(
        select(ShopifyOrder)
        .where(ShopifyOrder.shop_domain == shop_domain, ShopifyOrder.order_id == oid)
        .with_for_update()
    )


def _remove(db: Session, entry: ShopifyOrder, wanted: dict[str, float]) -> dict[str, float]:
    """Subtract up to `wanted` units per SKU from the order's sales day, capped at what the order still contributes.
    Returns what was actually removed."""
    removed = dict(entry.removed or {})
    applied: dict[str, float] = {}
    for sku, q in wanted.items():
        remaining = float(entry.lines.get(sku, 0.0)) - float(removed.get(sku, 0.0))
        take = min(float(q), remaining)
        if take <= 0:
            continue
        product_id = select(Product.id).where(Product.org_id == entry.org_id, Product.sku == sku).scalar_subquery()
        db.execute(
            update(SalesRecord)
            .where(
                SalesRecord.product_id == product_id,
                SalesRecord.sale_date == entry.order_date,
                SalesRecord.source == SOURCE,
            )
            .values(quantity=func.greatest(SalesRecord.quantity - take, 0.0))
        )
        removed[sku] = float(removed.get(sku, 0.0)) + take
        applied[sku] = take
    entry.removed = removed
    return applied


def _cancel_order(db: Session, shop_domain: str, order: dict[str, Any]) -> dict[str, Any]:
    entry = _ledger(db, shop_domain, order.get("id"))
    if entry is None:
        return {"ignored": "order not recorded"}
    if entry.cancelled_at is not None:
        return {"ignored": "already cancelled"}
    applied = _remove(db, entry, {sku: float(q) for sku, q in entry.lines.items()})
    entry.cancelled_at = datetime.now(UTC)
    return {"cancelled": True, "units_removed": sum(applied.values())}


def _ingest_refund(db: Session, shop_domain: str, refund: dict[str, Any]) -> dict[str, Any]:
    entry = _ledger(db, shop_domain, refund.get("order_id"))
    if entry is None:
        return {"ignored": "order not recorded"}
    refund_id = str(refund.get("id") or "").strip()[:40]
    if refund_id and refund_id in (entry.refund_ids or []):
        return {"ignored": "refund already applied"}
    wanted: dict[str, float] = {}
    kept = 0.0
    for rli in refund.get("refund_line_items") or []:
        q = rli.get("quantity")
        sku = _sku(rli.get("line_item") or {})
        if not sku or not isinstance(q, int | float) or isinstance(q, bool) or q <= 0:
            continue
        if rli.get("restock_type") in RESTOCKED:
            wanted[sku] = wanted.get(sku, 0.0) + float(q)
        else:
            kept += float(q)
    applied = _remove(db, entry, wanted)
    if refund_id:
        entry.refund_ids = [*(entry.refund_ids or []), refund_id]
    return {"units_removed": sum(applied.values()), "units_kept_as_sales": kept}


def handle_webhook(
    db: Session, *, topic: str, shop_domain: str | None, webhook_id: str | None, triggered_at: str | None, body: bytes
) -> dict[str, Any]:
    """Process one verified delivery. The caller has already checked the HMAC. Runs in the caller's transaction."""
    import json

    s = get_settings()
    if topic not in SUPPORTED_TOPICS:
        raise NotFound("Unsupported webhook topic")
    if not webhook_id or len(webhook_id) > 200:
        raise ValidationFailed("Missing X-Shopify-Webhook-Id")
    shop_domain = normalise_shop(shop_domain or "")
    if triggered_at:
        try:
            t = datetime.fromisoformat(triggered_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationFailed("Invalid X-Shopify-Triggered-At") from exc
        if t.tzinfo is None:
            t = t.replace(tzinfo=UTC)
        if datetime.now(UTC) - t > timedelta(seconds=s.shopify_webhook_tolerance_s):
            raise Forbidden("Webhook delivery is too old")

    ins = (
        insert(WebhookEvent)
        .values(provider="shopify", external_id=webhook_id, topic=topic, shop_domain=shop_domain, status="processing")
        .on_conflict_do_nothing(constraint="uq_webhook_provider_external")
        .returning(WebhookEvent.id)
    )
    event_id = db.execute(ins).scalar()
    if event_id is None:
        return {"duplicate": True}
    event = db.get(WebhookEvent, event_id)
    assert event is not None

    shop = db.scalar(select(ShopifyShop).where(ShopifyShop.shop_domain == shop_domain))
    details: dict[str, Any]
    if topic in ("customers/data_request", "customers/redact"):
        details = {"note": "Aurora stores no Shopify customer data"}
    elif shop is None:
        details = {"ignored": "shop not connected"}
    elif topic in ("app/uninstalled", "shop/redact"):
        _mark_uninstalled(shop)
        details = {"uninstalled": True}
    elif shop.uninstalled_at is not None:
        details = {"ignored": "shop disconnected"}
    else:
        try:
            order = json.loads(body)
        except ValueError as exc:
            raise ValidationFailed("Webhook body is not JSON") from exc
        if not isinstance(order, dict):
            raise ValidationFailed("Webhook body must be an object")
        if topic == "orders/cancelled":
            details = _cancel_order(db, shop.shop_domain, order)
        elif topic == "refunds/create":
            details = _ingest_refund(db, shop.shop_domain, order)
        else:
            details = _ingest_order(db, shop.org_id, shop.shop_domain, order)
    event.status = "processed"
    event.details = details
    event.processed_at = datetime.now(UTC)
    return {"duplicate": False, **details}


def reencrypt_tokens(db: Session) -> dict[str, Any]:
    """Re-encrypt every stored access token with the newest TOKEN_ENCRYPTION_KEYS key, so older keys can be removed.
    A token no configured key can decrypt is reported, not changed; that shop must be reconnected."""
    rotated, failed = 0, []
    for shop in db.scalars(select(ShopifyShop).order_by(ShopifyShop.shop_domain)).all():
        if not shop.access_token_enc:
            continue
        try:
            shop.access_token_enc = crypto.rotate(shop.access_token_enc)
            rotated += 1
        except ValueError:
            failed.append(shop.shop_domain)
    db.commit()
    return {"rotated": rotated, "failed": failed}
