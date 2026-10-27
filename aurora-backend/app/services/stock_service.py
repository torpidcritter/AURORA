"""Stock by location.

``StockLevel`` rows hold on-hand and on-order units per product and location. ``Product.on_hand`` and ``on_order``
stay equal to their sums, so every forecast, reorder and report that reads product totals keeps working unchanged.
All stock changes go through this module, which locks the product row first (the same lock purchasing uses), so the
levels and totals cannot drift apart under concurrent edits.

Demand is forecast for the organisation as a whole: sales records carry no location, so reorder suggestions stay
organisation-wide and locations describe where the stock is.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import Location, Product, PurchaseOrder, StockLevel, StockTransfer

DEFAULT_CODE = "MAIN"


# --- locations --------------------------------------------------------------------------------------------------------


def default_location(db: Session, org_id: uuid.UUID) -> Location:
    """The organisation's default location, created ("Main") on first use."""
    loc = db.scalar(select(Location).where(Location.org_id == org_id, Location.is_default.is_(True)))
    if loc is not None:
        return loc
    loc = Location(org_id=org_id, code=DEFAULT_CODE, name="Main", is_default=True)
    try:
        with db.begin_nested():
            db.add(loc)
            db.flush()
    except IntegrityError:  # created concurrently
        loc = db.scalar(select(Location).where(Location.org_id == org_id, Location.is_default.is_(True)))
        if loc is None:
            raise
    return loc


def get_location(db: Session, org_id: uuid.UUID, location_id: uuid.UUID) -> Location:
    loc = db.get(Location, location_id)
    if loc is None or loc.org_id != org_id:
        raise NotFound("Location not found")
    return loc


def resolve_location(db: Session, org_id: uuid.UUID, location_id: uuid.UUID | None, *, purpose: str) -> Location:
    """The given location, or the default when the organisation has only one active location.

    With several active locations a stock change must say where it happens, so nothing lands in the wrong place.
    """
    if location_id is not None:
        loc = get_location(db, org_id, location_id)
        if not loc.is_active:
            raise ValidationFailed(f"Location {loc.code} is inactive")
        return loc
    active = db.scalar(select(func.count()).where(Location.org_id == org_id, Location.is_active.is_(True))) or 0
    if active > 1:
        raise ValidationFailed(f"Choose a location for {purpose}: this organisation has {active} active locations")
    return default_location(db, org_id)


def location_out(loc: Location, totals: dict | None = None) -> dict:
    t = totals or {}
    return {
        "id": str(loc.id),
        "code": loc.code,
        "name": loc.name,
        "address": loc.address,
        "is_default": loc.is_default,
        "is_active": loc.is_active,
        "version": loc.version,
        "products": int(t.get("products") or 0),
        "on_hand": int(t.get("on_hand") or 0),
        "on_order": int(t.get("on_order") or 0),
        "stock_value": round(float(t["value"]), 2) if t.get("value") is not None else None,
    }


def list_locations(db: Session, org_id: uuid.UUID, include_inactive: bool = False) -> list[dict]:
    default_location(db, org_id)
    totals = {
        lid: {"products": n, "on_hand": oh, "on_order": oo, "value": v}
        for lid, n, oh, oo, v in db.execute(
            select(
                StockLevel.location_id,
                func.count().filter(StockLevel.on_hand > 0),
                func.sum(StockLevel.on_hand),
                func.sum(StockLevel.on_order),
                func.sum(StockLevel.on_hand * Product.unit_cost),
            )
            .join(Product, Product.id == StockLevel.product_id)
            .where(StockLevel.org_id == org_id)
            .group_by(StockLevel.location_id)
        ).all()
    }
    q = select(Location).where(Location.org_id == org_id)
    if not include_inactive:
        q = q.where(Location.is_active.is_(True))
    rows = db.scalars(q.order_by(Location.is_default.desc(), func.lower(Location.code))).all()
    return [location_out(loc, totals.get(loc.id)) for loc in rows]


def _code_taken(exc: IntegrityError) -> bool:
    return "uq_locations_org_code" in str(exc.orig)


def create_location(db: Session, org_id: uuid.UUID, values: dict) -> Location:
    default_location(db, org_id)  # make sure the existing stock has a home before a second location appears
    loc = Location(org_id=org_id, is_default=False, **values)
    try:
        with db.begin_nested():
            db.add(loc)
            db.flush()
    except IntegrityError as exc:
        if _code_taken(exc):
            raise Conflict("A location with this code already exists", code="location_exists") from exc
        raise
    return loc


def update_location(db: Session, org_id: uuid.UUID, location_id: uuid.UUID, changes: dict) -> Location:
    expected = changes.pop("expected_version")
    loc = db.scalar(
        select(Location)
        .where(Location.id == location_id, Location.org_id == org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if loc is None:
        raise NotFound("Location not found")
    if loc.version != expected:
        raise Conflict("The location was changed by someone else. Reload and try again.")
    if changes.get("is_active") is False and loc.is_active:
        if loc.is_default:
            raise ValidationFailed(
                "The default location cannot be deactivated; make another location the default first"
            )
        held = db.scalar(
            select(func.count()).where(
                StockLevel.location_id == loc.id, (StockLevel.on_hand > 0) | (StockLevel.on_order > 0)
            )
        )
        if held:
            raise ValidationFailed("This location still holds or expects stock; transfer or receive it first")
        open_orders = db.scalar(
            select(func.count()).where(
                PurchaseOrder.location_id == loc.id,
                PurchaseOrder.status.in_(("draft", "approved", "sent", "partially_received")),
            )
        )
        if open_orders:
            raise ValidationFailed("Open purchase orders deliver to this location")
    if changes.pop("is_default", None) is True and not loc.is_default:
        if not changes.get("is_active", loc.is_active):
            raise ValidationFailed("An inactive location cannot be the default")
        current = default_location(db, org_id)
        current.is_default = False
        current.version += 1
        db.flush()  # free the one-default-per-organisation slot before taking it
        loc.is_default = True
    for k, v in changes.items():
        setattr(loc, k, v)
    loc.version += 1
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        if _code_taken(exc):
            raise Conflict("A location with this code already exists", code="location_exists") from exc
        raise
    return loc


# --- stock levels -----------------------------------------------------------------------------------------------------


def lock_product(db: Session, org_id: uuid.UUID, product_id: uuid.UUID) -> Product:
    p = db.scalar(
        select(Product)
        .where(Product.id == product_id, Product.org_id == org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if p is None:
        raise NotFound("Product not found")
    return p


def _levels(db: Session, product: Product) -> dict[uuid.UUID, StockLevel]:
    """The product's levels by location. A product with totals but no levels (stock set before locations existed)
    gets them assigned to the default location first, so no units are lost."""
    levels = {
        lv.location_id: lv
        for lv in db.scalars(
            select(StockLevel).where(StockLevel.product_id == product.id).execution_options(populate_existing=True)
        )
    }
    if not levels and (product.on_hand or product.on_order):
        loc = default_location(db, product.org_id)
        lv = StockLevel(
            org_id=product.org_id,
            product_id=product.id,
            location_id=loc.id,
            on_hand=max(product.on_hand, 0),
            on_order=max(product.on_order, 0),
        )
        db.add(lv)
        levels[loc.id] = lv
    return levels


def _level(db: Session, product: Product, levels: dict, location_id: uuid.UUID) -> StockLevel:
    lv = levels.get(location_id)
    if lv is None:
        lv = StockLevel(org_id=product.org_id, product_id=product.id, location_id=location_id, on_hand=0, on_order=0)
        db.add(lv)
        levels[location_id] = lv
    return lv


def _sync_totals(product: Product, levels: dict) -> None:
    product.on_hand = sum(lv.on_hand for lv in levels.values())
    product.on_order = sum(lv.on_order for lv in levels.values())


def set_level(
    db: Session,
    product: Product,
    location_id: uuid.UUID,
    *,
    on_hand: int | None = None,
    on_order: int | None = None,
    cache: dict | None = None,
) -> None:
    """Set absolute values at one location (stock counts, snapshot uploads). The caller holds the product lock.

    ``cache`` (product id -> levels) lets a bulk import read each product's levels once.
    """
    if cache is not None:
        if product.id not in cache:
            cache[product.id] = _levels(db, product)
        levels = cache[product.id]
    else:
        levels = _levels(db, product)
    lv = _level(db, product, levels, location_id)
    if on_hand is not None:
        lv.on_hand = int(on_hand)
        product.inventory_known = True
    if on_order is not None:
        lv.on_order = int(on_order)
    _sync_totals(product, levels)
    if cache is None:
        db.flush()


def adjust(db: Session, product: Product, location_id: uuid.UUID, *, on_hand: int = 0, on_order: int = 0) -> None:
    """Add (or remove) units at one location; on-order never goes below zero. The caller holds the product lock."""
    levels = _levels(db, product)
    lv = _level(db, product, levels, location_id)
    if lv.on_hand + on_hand < 0:
        raise ValidationFailed("Not enough stock at this location")
    lv.on_hand += on_hand
    lv.on_order = max(lv.on_order + on_order, 0)
    _sync_totals(product, levels)
    db.flush()


def levels_for_product(db: Session, org_id: uuid.UUID, product_id: uuid.UUID) -> list[dict]:
    product = lock_product(db, org_id, product_id)  # brief lock so a legacy product's levels are created once
    levels = _levels(db, product)
    db.flush()
    locs = {loc.id: loc for loc in db.scalars(select(Location).where(Location.org_id == org_id))}
    out = []
    for loc_id, loc in sorted(locs.items(), key=lambda kv: (not kv[1].is_default, kv[1].code.lower())):
        lv = levels.get(loc_id)
        if lv is None and not loc.is_active:
            continue
        out.append(
            {
                "location_id": str(loc_id),
                "code": loc.code,
                "name": loc.name,
                "is_default": loc.is_default,
                "is_active": loc.is_active,
                "on_hand": lv.on_hand if lv else 0,
                "on_order": lv.on_order if lv else 0,
            }
        )
    return out


def transfer(
    db: Session,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    product_id: uuid.UUID,
    from_location_id: uuid.UUID,
    to_location_id: uuid.UUID,
    quantity: int,
    note: str | None = None,
) -> StockTransfer:
    if from_location_id == to_location_id:
        raise ValidationFailed("Pick two different locations")
    src = get_location(db, org_id, from_location_id)
    dst = get_location(db, org_id, to_location_id)
    if not dst.is_active:
        raise ValidationFailed(f"Location {dst.code} is inactive")
    product = lock_product(db, org_id, product_id)
    levels = _levels(db, product)
    available = levels[src.id].on_hand if src.id in levels else 0
    if quantity > available:
        raise ValidationFailed(f"Only {available} on hand at {src.code}", details={"available": available})
    levels[src.id].on_hand -= quantity
    _level(db, product, levels, dst.id).on_hand += quantity
    _sync_totals(product, levels)  # unchanged, but keeps the invariant obvious
    product.version += 1
    t = StockTransfer(
        org_id=org_id,
        product_id=product.id,
        from_location_id=src.id,
        to_location_id=dst.id,
        quantity=quantity,
        note=(note or "").strip()[:500] or None,
        created_by=user_id,
    )
    db.add(t)
    db.flush()
    return t


def list_transfers(
    db: Session, org_id: uuid.UUID, *, product_id: uuid.UUID | None = None, limit: int = 50, offset: int = 0
) -> tuple[list[dict], int]:
    q = (
        select(StockTransfer, Product.sku)
        .join(Product, Product.id == StockTransfer.product_id)
        .where(StockTransfer.org_id == org_id)
    )
    if product_id is not None:
        q = q.where(StockTransfer.product_id == product_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    codes = dict(db.execute(select(Location.id, Location.code).where(Location.org_id == org_id)).tuples().all())
    rows = db.execute(q.order_by(StockTransfer.created_at.desc()).limit(limit).offset(offset)).all()
    return [
        {
            "id": str(t.id),
            "product_id": str(t.product_id),
            "sku": sku,
            "from_location_id": str(t.from_location_id),
            "from_code": codes.get(t.from_location_id),
            "to_location_id": str(t.to_location_id),
            "to_code": codes.get(t.to_location_id),
            "quantity": t.quantity,
            "note": t.note,
            "created_by": str(t.created_by) if t.created_by else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t, sku in rows
    ], total
