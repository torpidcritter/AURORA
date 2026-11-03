"""Scheduled imports from other systems (ADR-050).

Two kinds of source:
- ``file_url``: a CSV, Excel or JSON file at an HTTPS address (an export a POS or ERP publishes, a published
  spreadsheet, a file in object storage behind a signed link). Each run downloads it and passes it through the same
  pipeline as a manual upload, so mapping, validation, quality report and dataset history are identical. An
  unchanged file is recognised by its hash and skipped.
- ``woocommerce``: orders from a WooCommerce store's REST API (read-only consumer key). Each run re-reads orders
  created in the last ``lookback_days`` days and replaces this source's sales for those days, so cancellations,
  status changes and late edits inside the window are picked up and running the import twice changes nothing.

Every address is checked against private networks before it is saved and again before every request, redirects are
not followed, responses are size-limited, and addresses and credentials are encrypted at rest. A source that fails
``PAUSE_AFTER_FAILURES`` times in a row is paused and raises an alert, so a revoked key does not fail silently.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import crypto, outbound
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import Dataset, ImportRun, ImportSource, Product, SalesRecord
from app.services import alert_service, audit, dataset_service

log = logging.getLogger("aurora.imports")

TIMEOUT_S = 30.0
MAX_SOURCES_PER_ORG = 20
PAUSE_AFTER_FAILURES = 5
RUN_RETENTION = timedelta(days=90)
WOO_PAGE_SIZE = 100
WOO_MAX_PAGES = 50  # 5,000 orders per run; a busier store needs a shorter look-back window
WOO_COUNTED = frozenset({"processing", "completed", "on-hold"})
WOO_DEFAULT_LOOKBACK = 14
FORMATS = {"text/csv": ".csv", "application/json": ".json", "application/vnd.ms-excel": ".xls"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ImportFailed(Exception):
    """A run failed for a reason the person who set the source up can act on."""


def _now() -> datetime:
    return datetime.now(UTC)


def _client() -> httpx.Client:
    """The HTTP client for imports. Tests replace this with one backed by a fake server."""
    return httpx.Client(timeout=TIMEOUT_S, follow_redirects=False, headers={"User-Agent": "Aurora-Import/1"})


def next_run(frequency: str, hour_utc: int, after: datetime) -> datetime:
    """The next slot strictly after ``after``: the next whole hour, or the next ``hour_utc`` o'clock."""
    base = after.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    if frequency == "hourly":
        return base + timedelta(hours=1)
    slot = base.replace(hour=hour_utc)
    return slot if slot > after else slot + timedelta(days=1)


def sales_source(s: ImportSource) -> str:
    """The ``sales_records.source`` value for a WooCommerce store's sales; distinct per store so one store's
    window replacement never touches another's sales."""
    return f"woo:{s.id.hex[:12]}"


# ---- validation ---------------------------------------------------------------------------------------------------


def _hint(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname}/…"[:300]


def _check_address(kind: str, url: str | None) -> str:
    if not url or not url.strip():
        raise ValidationFailed("Enter the address to import from")
    url = outbound.check_url(url)
    parts = urlsplit(url)
    if kind == "woocommerce":
        if parts.query or parts.fragment:
            raise ValidationFailed("Enter the store's address only, for example https://shop.example.com")
        url = url.rstrip("/")
    return url


def _check_secret(kind: str, credential: dict | None) -> str | None:
    credential = credential or {}
    if kind == "woocommerce":
        key, secret = (credential.get("consumer_key") or "").strip(), (credential.get("consumer_secret") or "").strip()
        if not key or not secret:
            raise ValidationFailed("Enter the store's consumer key and consumer secret (read access is enough)")
        for v in (key, secret):
            if len(v) > 200 or any(c.isspace() or c == ":" for c in v):
                raise ValidationFailed("The consumer key and secret must be single words without colons")
        return f"{key}:{secret}"
    header = (credential.get("authorization") or "").strip()
    if not header:
        return None
    if len(header) > 2000 or any(c in header for c in "\r\n"):
        raise ValidationFailed("The Authorization header must be a single line of at most 2,000 characters")
    return header


def _check_options(kind: str, options: dict | None) -> dict:
    options = options or {}
    if kind == "woocommerce":
        days = options.get("lookback_days", WOO_DEFAULT_LOOKBACK)
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 90:
            raise ValidationFailed("The look-back window must be between 1 and 90 days")
        return {"lookback_days": days}
    policy = options.get("missing_policy", "zero")
    if policy not in ("zero", "interpolate"):
        raise ValidationFailed("Missing days must be treated as zero sales or interpolated")
    return {"missing_policy": policy}


# ---- sources ------------------------------------------------------------------------------------------------------


def source_out(s: ImportSource) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "name": s.name,
        "kind": s.kind,
        "url_hint": s.url_hint,
        "has_credential": s.secret_encrypted is not None,
        "options": s.options,
        "frequency": s.frequency,
        "hour_utc": s.hour_utc,
        "is_active": s.is_active,
        "next_run_at": s.next_run_at.isoformat() if s.is_active else None,
        "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
        "last_status": s.last_status,
        "last_message": s.last_message,
        "consecutive_failures": s.consecutive_failures,
        "version": s.version,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def list_sources(db: Session, org_id: uuid.UUID) -> list[ImportSource]:
    return list(db.scalars(select(ImportSource).where(ImportSource.org_id == org_id).order_by(ImportSource.name)))


def get_source(db: Session, org_id: uuid.UUID, source_id: uuid.UUID, *, lock: bool = False) -> ImportSource:
    q = select(ImportSource).where(ImportSource.id == source_id, ImportSource.org_id == org_id)
    s = db.scalar(q.with_for_update() if lock else q)
    if s is None:
        raise NotFound("Import source not found")
    return s


def create_source(db: Session, org_id: uuid.UUID, user_id: uuid.UUID | None, v: dict) -> ImportSource:
    count = len(list_sources(db, org_id))
    if count >= MAX_SOURCES_PER_ORG:
        raise ValidationFailed(f"An organisation can have at most {MAX_SOURCES_PER_ORG} import sources")
    kind = v["kind"]
    url = _check_address(kind, v.get("url"))
    secret = _check_secret(kind, v.get("credential"))
    s = ImportSource(
        id=uuid.uuid4(),
        org_id=org_id,
        name=v["name"].strip(),
        kind=kind,
        url_encrypted=crypto.encrypt(url),
        url_hint=_hint(url),
        secret_encrypted=crypto.encrypt(secret) if secret else None,
        options=_check_options(kind, v.get("options")),
        frequency=v.get("frequency", "daily"),
        hour_utc=v.get("hour_utc", 5),
        is_active=True,
        created_by=user_id,
        version=1,
    )
    s.next_run_at = next_run(s.frequency, s.hour_utc, _now())
    db.add(s)
    db.flush()
    return s


def update_source(db: Session, org_id: uuid.UUID, source_id: uuid.UUID, changes: dict) -> ImportSource:
    s = get_source(db, org_id, source_id, lock=True)
    if s.version != changes.pop("expected_version"):
        raise Conflict("This import was changed by someone else; reload and try again", code="version_conflict")
    if changes.get("url") is not None:
        url = _check_address(s.kind, changes["url"])
        s.url_encrypted, s.url_hint = crypto.encrypt(url), _hint(url)
    if changes.get("credential") is not None:
        secret = _check_secret(s.kind, changes["credential"])
        s.secret_encrypted = crypto.encrypt(secret) if secret else None
    if changes.get("options") is not None:
        s.options = _check_options(s.kind, changes["options"])
    for k in ("name", "frequency", "hour_utc", "is_active"):
        if changes.get(k) is not None:
            setattr(s, k, changes[k].strip() if k == "name" else changes[k])
    if changes.get("is_active"):
        s.consecutive_failures = 0  # resuming after a pause starts the count again
    s.next_run_at = next_run(s.frequency, s.hour_utc, _now())
    s.version += 1
    return s


def delete_source(db: Session, org_id: uuid.UUID, source_id: uuid.UUID, *, remove_sales: bool) -> int:
    """Delete a source. Its imported sales stay unless ``remove_sales``; returns how many sales rows were removed."""
    s = get_source(db, org_id, source_id, lock=True)
    removed = 0
    if remove_sales:
        if s.kind == "woocommerce":
            res = db.execute(
                delete(SalesRecord).where(SalesRecord.org_id == org_id, SalesRecord.source == sales_source(s))
            )
        else:
            # Rows a later manual upload overwrote point at that upload's dataset and are kept.
            datasets = select(Dataset.id).where(
                Dataset.org_id == org_id, Dataset.tags["import_source"].astext == str(s.id)
            )
            res = db.execute(
                delete(SalesRecord).where(SalesRecord.org_id == org_id, SalesRecord.dataset_id.in_(datasets))
            )
        removed = int(res.rowcount or 0)  # type: ignore[attr-defined]
    db.delete(s)
    return removed


# ---- fetching -----------------------------------------------------------------------------------------------------


def _check_response(r: httpx.Response, what: str) -> None:
    if r.is_redirect:
        raise ImportFailed(f"{what} answered with a redirect ({r.status_code}); use the final address instead")
    if r.status_code in (401, 403):
        raise ImportFailed(f"{what} refused access (HTTP {r.status_code}); check the credential")
    if r.status_code == 404:
        raise ImportFailed(f"{what} answered 404 Not Found; check the address")
    if r.status_code >= 400:
        raise ImportFailed(f"{what} answered HTTP {r.status_code}")


def _filename(url: str, content_type: str) -> str:
    name = urlsplit(url).path.rsplit("/", 1)[-1] or "import"
    if "." in name and name.rsplit(".", 1)[1].lower() in ("csv", "json", "xlsx", "xls"):
        return name
    ct = content_type.split(";")[0].strip().lower()
    return name + (".xlsx" if ct == XLSX else FORMATS.get(ct, ".csv"))


def fetch_file(url: str, authorization: str | None, client: httpx.Client) -> tuple[bytes, str]:
    outbound.check_url(url)
    limit = get_settings().max_upload_bytes
    headers = {"Authorization": authorization} if authorization else {}
    with client.stream("GET", url, headers=headers) as r:
        _check_response(r, "The file server")
        buf = bytearray()
        for chunk in r.iter_bytes():
            buf += chunk
            if len(buf) > limit:
                raise ImportFailed(f"The file is larger than the {limit // (1024 * 1024)} MB upload limit")
        return bytes(buf), _filename(url, r.headers.get("content-type", ""))


def fetch_woo_orders(store: str, key_secret: str, since: datetime, client: httpx.Client) -> list[dict]:
    endpoint = f"{store}/wp-json/wc/v3/orders"
    outbound.check_url(endpoint)
    key, _, secret = key_secret.partition(":")
    orders: list[dict] = []
    page = 1
    while True:
        params = {
            "per_page": WOO_PAGE_SIZE,
            "page": page,
            "after": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            "dates_are_gmt": "true",
            "orderby": "date",
            "order": "asc",
        }
        r = client.get(endpoint, params=params, auth=httpx.BasicAuth(key, secret))
        _check_response(r, "The store")
        try:
            body = r.json()
        except ValueError as exc:
            raise ImportFailed("The store did not answer with JSON; is this a WooCommerce store?") from exc
        if not isinstance(body, list):
            raise ImportFailed("The store's answer was not a list of orders")
        orders.extend(o for o in body if isinstance(o, dict))
        try:
            pages = int(r.headers.get("x-wp-totalpages", "1"))
        except ValueError:
            pages = 1
        if pages > WOO_MAX_PAGES:
            raise ImportFailed(
                f"More than {WOO_MAX_PAGES * WOO_PAGE_SIZE:,} orders in the look-back window; shorten the window"
            )
        if page >= pages or not body:
            return orders
        page += 1


def _order_day(order: dict) -> date | None:
    # date_created is the store's local time, which is the sales day a shopkeeper means.
    raw = order.get("date_created") or order.get("date_created_gmt")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def aggregate_woo(orders: list[dict], start: date) -> tuple[dict[tuple[str, date], float], dict[str, str], int]:
    """Units per (SKU, day) from counted orders on or after ``start``; also product names and skipped lines."""
    qty: dict[tuple[str, date], float] = defaultdict(float)
    names: dict[str, str] = {}
    skipped = 0
    for o in orders:
        if o.get("status") not in WOO_COUNTED:
            continue
        day = _order_day(o)
        if day is None or day < start:
            continue
        for li in o.get("line_items") or []:
            sku = str(li.get("sku") or "").strip()[:120]
            q = li.get("quantity")
            if not sku or not isinstance(q, int | float) or isinstance(q, bool) or q <= 0:
                skipped += 1
                continue
            qty[(sku, day)] += float(q)
            names.setdefault(sku, str(li.get("name") or sku)[:300])
    return dict(qty), names, skipped


def _replace_window(
    db: Session, s: ImportSource, start: date, qty: dict[tuple[str, date], float], names: dict[str, str]
) -> int:
    if names:
        # New SKUs start with unknown stock, like Shopify ones, so no recommendation is made until someone counts.
        stmt = insert(Product).values(
            [
                {"id": uuid.uuid4(), "org_id": s.org_id, "sku": k, "name": n, "inventory_known": False}
                for k, n in names.items()
            ]
        )
        db.execute(stmt.on_conflict_do_nothing(constraint="uq_products_org_sku"))
    tag = sales_source(s)
    db.execute(
        delete(SalesRecord).where(
            SalesRecord.org_id == s.org_id, SalesRecord.source == tag, SalesRecord.sale_date >= start
        )
    )
    if not qty:
        return 0
    ids = dict(
        db.execute(select(Product.sku, Product.id).where(Product.org_id == s.org_id, Product.sku.in_(list(names))))
        .tuples()
        .all()
    )
    rows = [
        {"org_id": s.org_id, "product_id": ids[sku], "sale_date": day, "quantity": q, "source": tag}
        for (sku, day), q in qty.items()
    ]
    db.execute(insert(SalesRecord).values(rows))
    return len(rows)


# ---- running ------------------------------------------------------------------------------------------------------


def _run_file(db: Session, s: ImportSource, run: ImportRun, client: httpx.Client) -> None:
    secret = crypto.decrypt(s.secret_encrypted) if s.secret_encrypted else None
    data, filename = fetch_file(crypto.decrypt(s.url_encrypted), secret, client)
    outcome = dataset_service.ingest_upload(
        db,
        org_id=s.org_id,
        user_id=None,
        filename=filename,
        data=data,
        tags={"import_source": str(s.id), "source": "scheduled import"},
        missing_policy=s.options.get("missing_policy", "zero"),
    )
    ds = outcome.dataset
    run.dataset_id = ds.id
    if ds.status == "rejected":
        # Not raised: the rejected dataset and its quality report are kept so the problem can be seen on the
        # Upload page. The same file again is still a failure, not "unchanged".
        reason = (ds.quality_report or {}).get("rejection_reason") or "No valid rows"
        run.status, run.message = "failed", f"The file was rejected: {reason}"
    elif outcome.duplicate:
        run.status, run.message = "unchanged", "The file has not changed since it was last imported"
    else:
        run.status, run.rows = "imported", ds.accepted_rows
        run.products = int((ds.quality_report or {}).get("products", 0) or 0)
        run.message = f"Imported {ds.accepted_rows:,} rows from {ds.filename}"


def _run_woo(db: Session, s: ImportSource, run: ImportRun, client: httpx.Client) -> None:
    days = int(s.options.get("lookback_days", WOO_DEFAULT_LOOKBACK))
    start = (_now() - timedelta(days=days)).date()
    since = datetime(start.year, start.month, start.day, tzinfo=UTC) - timedelta(days=1)  # local dates may lag UTC
    orders = fetch_woo_orders(crypto.decrypt(s.url_encrypted), crypto.decrypt(s.secret_encrypted or ""), since, client)
    qty, names, skipped = aggregate_woo(orders, start)
    run.rows = _replace_window(db, s, start, qty, names)
    run.products = len(names)
    run.status = "imported"
    note = f"; {skipped} line(s) without a SKU skipped" if skipped else ""
    run.message = (
        f"{len(orders):,} orders read, sales from {start.isoformat()} replaced ({run.rows} product-days){note}"
    )


def run(db: Session, s: ImportSource, *, trigger: str = "schedule", client: httpx.Client | None = None) -> ImportRun:
    """Run one import. Data changes and the run record are flushed together; a failure rolls the data back to a
    savepoint, so a half-read store never replaces a window with partial sales."""
    r = ImportRun(id=uuid.uuid4(), org_id=s.org_id, source_id=s.id, trigger=trigger, status="failed", started_at=_now())
    c = client or _client()
    try:
        with db.begin_nested():
            if s.kind == "woocommerce":
                _run_woo(db, s, r, c)
            else:
                _run_file(db, s, r, c)
    except (ImportFailed, ValidationFailed) as exc:
        r.status, r.message = "failed", str(exc)[:500]
    except httpx.HTTPError as exc:
        r.status, r.message = "failed", f"Could not reach the server ({type(exc).__name__})"
    finally:
        if client is None:
            c.close()
    r.finished_at = _now()
    db.add(r)
    s.last_run_at, s.last_status, s.last_message = r.finished_at, r.status, r.message
    if r.status == "failed":
        s.consecutive_failures += 1
        if s.consecutive_failures >= PAUSE_AFTER_FAILURES and s.is_active:
            s.is_active = False
            alert_service.upsert(
                db,
                org_id=s.org_id,
                kind="import_failed",
                severity="high",
                title=f"Import paused: {s.name}",
                message=f"It failed {s.consecutive_failures} times in a row; last error: {r.message}. "
                "Fix the address or credential, then resume it under Admin → Data imports.",
                dedup_key=f"import_failed:{s.id}",
                details={"source_id": str(s.id)},
            )
            audit.record(
                db,
                "import.paused",
                org_id=s.org_id,
                resource_type="import_source",
                resource_id=s.id,
                outcome="failure",
                details={"failures": s.consecutive_failures},
            )
    else:
        s.consecutive_failures = 0
    db.flush()
    return r


def run_due(db: Session, *, limit: int = 50, client: httpx.Client | None = None) -> dict[str, int]:
    """Run every import whose slot has come. Each source is claimed and moved to its next slot before it runs, so
    concurrent workers skip it and a crash does not repeat it in a loop."""
    stats = {"imported": 0, "unchanged": 0, "failed": 0}
    for _ in range(limit):
        s = db.scalar(
            select(ImportSource)
            .where(ImportSource.is_active, ImportSource.next_run_at <= _now())
            .order_by(ImportSource.next_run_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if s is None:
            break
        s.next_run_at = next_run(s.frequency, s.hour_utc, max(_now(), s.next_run_at))
        db.commit()
        try:
            r = run(db, s, client=client)
            stats[r.status] += 1
            db.commit()
        except Exception:  # noqa: BLE001 - one organisation's broken source must not stop the others
            db.rollback()
            stats["failed"] += 1
            log.exception("import failed", extra={"extra_fields": {"source_id": str(s.id)}})
    return stats


def run_out(r: ImportRun) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "trigger": r.trigger,
        "status": r.status,
        "rows": r.rows,
        "products": r.products,
        "dataset_id": str(r.dataset_id) if r.dataset_id else None,
        "message": r.message,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


def list_runs(db: Session, org_id: uuid.UUID, source_id: uuid.UUID, limit: int = 20) -> list[ImportRun]:
    get_source(db, org_id, source_id)
    q = (
        select(ImportRun)
        .where(ImportRun.org_id == org_id, ImportRun.source_id == source_id)
        .order_by(ImportRun.started_at.desc())
        .limit(limit)
    )
    return list(db.scalars(q))


def purge_old(db: Session, now: datetime | None = None) -> int:
    res = db.execute(delete(ImportRun).where(ImportRun.started_at < (now or _now()) - RUN_RETENTION))
    return int(res.rowcount or 0)  # type: ignore[attr-defined]
