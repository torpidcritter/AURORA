from __future__ import annotations

import io
import uuid
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import Location, Product, SalesRecord
from app.schemas.data import ProductOut
from app.services import stock_service

SORTABLE = {"sku", "name", "units_30d", "on_hand", "last_date", "category"}


def org_as_of(db: Session, org_id: uuid.UUID) -> date | None:
    """Latest sales date in the organisation: KPIs are computed relative to it, not wall-clock today,
    so historical datasets produce meaningful numbers."""
    return db.scalar(select(func.max(SalesRecord.sale_date)).where(SalesRecord.org_id == org_id))


def get_product(db: Session, org_id: uuid.UUID, product_id: uuid.UUID) -> Product:
    p = db.get(Product, product_id)
    if p is None or p.org_id != org_id:
        raise NotFound("Product not found")
    return p


def list_products(
    db: Session,
    org_id: uuid.UUID,
    *,
    q: str | None = None,
    category: str | None = None,
    sort: str = "sku",
    order: str = "asc",
    limit: int = 50,
    offset: int = 0,
    ids: list[uuid.UUID] | None = None,
) -> tuple[list[ProductOut], int]:
    as_of = org_as_of(db, org_id) or date.today()
    window_start = as_of - timedelta(days=29)
    stats = (
        select(
            SalesRecord.product_id.label("pid"),
            func.min(SalesRecord.sale_date).label("first_date"),
            func.max(SalesRecord.sale_date).label("last_date"),
            func.coalesce(func.sum(SalesRecord.quantity).filter(SalesRecord.sale_date >= window_start), 0.0).label(
                "units_30d"
            ),
        )
        .where(SalesRecord.org_id == org_id)
        .group_by(SalesRecord.product_id)
        .subquery()
    )
    base = (
        select(Product, stats.c.first_date, stats.c.last_date, stats.c.units_30d)
        .outerjoin(stats, stats.c.pid == Product.id)
        .where(Product.org_id == org_id)
    )
    if q:
        like = f"%{q.replace('%', '').replace('_', '')[:100]}%"
        base = base.where(or_(Product.sku.ilike(like), Product.name.ilike(like)))
    if category:
        base = base.where(Product.category == category)
    if ids is not None:
        base = base.where(Product.id.in_(ids))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    if sort not in SORTABLE:
        raise ValidationFailed(f"sort must be one of {sorted(SORTABLE)}")
    col = {
        "sku": Product.sku,
        "name": Product.name,
        "category": Product.category,
        "on_hand": Product.on_hand,
        "units_30d": stats.c.units_30d,
        "last_date": stats.c.last_date,
    }[sort]
    col = col.desc().nulls_last() if order == "desc" else col.asc().nulls_last()
    rows = db.execute(base.order_by(col, Product.sku).limit(limit).offset(offset)).all()
    return [_to_out(p, fd, ld, u) for p, fd, ld, u in rows], total


def _to_out(p: Product, first: date | None, last: date | None, units: float | None) -> ProductOut:
    return ProductOut(
        id=p.id,
        sku=p.sku,
        name=p.name,
        category=p.category,
        unit_cost=p.unit_cost,
        on_hand=p.on_hand,
        on_order=p.on_order,
        inventory_known=p.inventory_known,
        lead_time_days=p.lead_time_days,
        service_level=p.service_level,
        pack_size=p.pack_size,
        min_order_qty=p.min_order_qty,
        missing_policy=p.missing_policy,
        supplier_id=p.supplier_id,
        version=p.version,
        first_date=first,
        last_date=last,
        history_days=((last - first).days + 1) if first and last else 0,
        units_30d=round(float(units or 0.0), 3),
        avg_daily_30d=round(float(units or 0.0) / 30.0, 3),
    )


def product_out(db: Session, org_id: uuid.UUID, product_id: uuid.UUID) -> ProductOut:
    get_product(db, org_id, product_id)
    items, _ = list_products(db, org_id, ids=[product_id], limit=1)
    return items[0]


def update_inventory(db: Session, org_id: uuid.UUID, product_id: uuid.UUID, changes: dict) -> Product:
    """Compare-and-set on ``version`` under a row lock, so concurrent edits cannot silently overwrite each other.

    On-hand and on-order are counts at one location (``location_id``; optional while the organisation has a single
    active location); the product totals follow from the per-location levels.
    """
    expected = changes.pop("expected_version")
    location_id = changes.pop("location_id", None)
    p = stock_service.lock_product(db, org_id, product_id)
    if p.version != expected:
        raise Conflict("The product was modified by someone else. Reload and try again.")
    values = {k: v for k, v in changes.items() if v is not None}
    stock = {k: values.pop(k) for k in ("on_hand", "on_order") if k in values}
    for k, v in values.items():
        setattr(p, k, v)
    if stock:
        loc = stock_service.resolve_location(db, org_id, location_id, purpose="a stock count")
        stock_service.set_level(db, p, loc.id, **stock)
        p.inventory_known = True
    p.version += 1
    db.flush()
    return p


INVENTORY_ALIASES = {
    "sku": ["sku", "product_id", "item_code", "product", "item_id"],
    "on_hand": ["on_hand", "stock", "inventory", "qty_on_hand", "current_inventory", "stock_on_hand"],
    "on_order": ["on_order", "incoming", "open_po_qty"],
    "lead_time_days": ["lead_time_days", "lead_time", "supplier_lead_time"],
    "unit_cost": ["unit_cost", "cost"],
    "location": ["location", "location_code", "warehouse", "store", "site"],
}


def import_inventory_snapshot(db: Session, org_id: uuid.UUID, data: bytes) -> dict:
    try:
        df = pd.read_csv(io.StringIO(data.decode("utf-8-sig")), dtype=str, keep_default_na=False, nrows=200_001)
    except Exception as exc:  # noqa: BLE001
        raise ValidationFailed(f"Could not parse inventory CSV: {type(exc).__name__}") from exc
    if len(df) > 200_000:
        raise ValidationFailed("Inventory file has more than 200,000 rows")
    cols = {c.strip().lower().replace(" ", "_"): c for c in df.columns}
    mapping = {}
    for canon, aliases in INVENTORY_ALIASES.items():
        hit = next((cols[a] for a in aliases if a in cols), None)
        if hit:
            mapping[canon] = hit
    if "sku" not in mapping or "on_hand" not in mapping:
        raise ValidationFailed("Inventory file needs 'sku' and 'on_hand' columns", details={"found": list(df.columns)})
    locations = {
        loc.code.lower(): loc
        for loc in db.scalars(select(Location).where(Location.org_id == org_id, Location.is_active.is_(True)))
    }
    fallback = None
    if "location" not in mapping:
        fallback = stock_service.resolve_location(
            db, org_id, None, purpose="an inventory upload (add a location column)"
        )
    # Lock the organisation's products in id order (the order every stock change uses) for the whole import.
    products = {
        p.sku: p
        for p in db.scalars(
            select(Product)
            .where(Product.org_id == org_id)
            .order_by(Product.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    updated, unknown, errors = 0, [], []
    level_cache: dict = {}
    for i, row in df.iterrows():
        sku = str(row[mapping["sku"]]).strip()
        p = products.get(sku)
        if p is None:
            unknown.append(sku)
            continue
        try:
            # Parse the whole row first, so a bad value in one column applies nothing from that row.
            on_hand = int(float(row[mapping["on_hand"]]))
            if on_hand < 0:
                raise ValueError("negative")
            on_order = None
            if "on_order" in mapping and str(row[mapping["on_order"]]).strip():
                on_order = max(int(float(row[mapping["on_order"]])), 0)
            lt = None
            if "lead_time_days" in mapping and str(row[mapping["lead_time_days"]]).strip():
                lt = int(float(row[mapping["lead_time_days"]]))
                if not 1 <= lt <= 365:
                    raise ValueError("lead time out of range")
            cost = None
            if "unit_cost" in mapping and str(row[mapping["unit_cost"]]).strip():
                cost = max(float(row[mapping["unit_cost"]]), 0.0)
            if fallback is not None:
                loc = fallback
            else:
                code = str(row[mapping["location"]]).strip().lower()
                loc = locations.get(code)
                if loc is None:
                    raise ValueError(f"unknown or inactive location '{code[:40]}'")
            stock_service.set_level(db, p, loc.id, on_hand=on_hand, on_order=on_order, cache=level_cache)
            if lt is not None:
                p.lead_time_days = lt
            if cost is not None:
                p.unit_cost = cost
            p.inventory_known = True
            p.version += 1
            updated += 1
        except (ValueError, TypeError) as exc:
            errors.append({"row": int(i) + 2, "sku": sku, "message": str(exc)[:100]})  # type: ignore[call-overload]
    return {"updated": updated, "unknown_skus": unknown[:50], "unknown_count": len(unknown), "errors": errors[:50]}
