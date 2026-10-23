"""Shopify integration: OAuth install, HMAC-verified webhooks, idempotent order ingestion.

Flow
1. An admin calls `start_install(shop)`; we store a single-use, 10-minute `state` bound to their organisation.
2. Shopify redirects the browser to `/shopify/callback`. We verify the query HMAC, timestamp, shop domain and
   state (consumed atomically), exchange the code for an offline token and store it encrypted.
3. Shopify sends webhooks. We verify the body HMAC (current or previous secret, constant-time), reject stale
   deliveries, and record the webhook id in `webhook_events` in the same transaction as the sales update, so a
   redelivery is acknowledged without being applied twice.

Order line items with a SKU become daily `sales_records` rows with `source="shopify"`. Orders are added on
`orders/create`; edits, cancellations and refunds are not reconciled (documented limitation).
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
from urllib.parse import urlencode

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import crypto
from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden, NotFound, Unavailable, ValidationFailed
from app.db.models import OAuthState, Product, SalesRecord, ShopifyShop, WebhookEvent

SHOP_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com$")
STATE_TTL = timedelta(minutes=10)
SOURCE = "shopify"
SUPPORTED_TOPICS = {"orders/create", "app/uninstalled", "customers/data_request", "customers/redact", "shop/redact"}


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


def complete_install(db: Session, params: dict[str, str], http: httpx.Client | None = None) -> ShopifyShop:
    s = get_settings()
    _require_config()
    if not verify_query_hmac(params):
        raise Forbidden("Invalid Shopify signature")
    try:
        ts = int(params.get("timestamp", "0"))
    except ValueError as exc:
        raise Forbidden("Invalid timestamp") from exc
    if abs(datetime.now(UTC).timestamp() - ts) > s.shopify_webhook_tolerance_s:
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


def _ingest_order(db: Session, org_id: uuid.UUID, order: dict[str, Any]) -> dict[str, Any]:
    if order.get("test") is True:
        return {"skipped": "test order"}
    day = _order_date(order)
    qty_by_sku: dict[str, float] = {}
    names: dict[str, str] = {}
    skipped = 0
    for li in order.get("line_items") or []:
        sku = str(li.get("sku") or "").strip()[:120]
        q = li.get("quantity")
        if not sku or not isinstance(q, int | float) or q <= 0:
            skipped += 1
            continue
        qty_by_sku[sku] = qty_by_sku.get(sku, 0.0) + float(q)
        names.setdefault(sku, str(li.get("title") or sku)[:255])
    if not qty_by_sku:
        return {"lines": 0, "skipped_lines": skipped}
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
        details = _ingest_order(db, shop.org_id, order)
    event.status = "processed"
    event.details = details
    event.processed_at = datetime.now(UTC)
    return {"duplicate": False, **details}
