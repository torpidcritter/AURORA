"""Suppliers and purchase orders.

Lifecycle: draft -> approved -> sent -> (partially_received ->) received, or cancelled before it is fully received.

- Lines change only while the order is a draft.
- Approving needs a supplier and at least one line, and a separate permission (analyst or admin).
- Sending adds each line's quantity to the product's ``on_order``.
- Receiving moves the quantity received from ``on_order`` to ``on_hand``; an order can be received in parts.
- Cancelling a sent order removes what is still outstanding from ``on_order``.

Each transition locks the order row, so two people pressing the same button cannot apply it twice, and locks the
affected products in id order, so concurrent orders for the same products cannot deadlock.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import Organization, Product, PurchaseOrder, PurchaseOrderLine, Supplier
from app.services import inventory_service, settings_service

OPEN_UNSENT = ("draft", "approved")
OPEN = ("draft", "approved", "sent", "partially_received")
MAX_LINES = 500


def _now() -> datetime:
    return datetime.now(UTC)


# --- suppliers --------------------------------------------------------------------------------------------------------


def get_supplier(db: Session, org_id: uuid.UUID, supplier_id: uuid.UUID) -> Supplier:
    s = db.get(Supplier, supplier_id)
    if s is None or s.org_id != org_id:
        raise NotFound("Supplier not found")
    return s


def supplier_out(s: Supplier, product_count: int | None = None) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "contact_name": s.contact_name,
        "email": s.email,
        "phone": s.phone,
        "lead_time_days": s.lead_time_days,
        "min_order_value": s.min_order_value,
        "notes": s.notes,
        "is_active": s.is_active,
        "version": s.version,
        "product_count": product_count,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def list_suppliers(
    db: Session, org_id: uuid.UUID, *, q: str | None = None, include_inactive: bool = False
) -> list[dict]:
    counts = (
        select(Product.supplier_id, func.count().label("n"))
        .where(Product.org_id == org_id, Product.supplier_id.is_not(None))
        .group_by(Product.supplier_id)
        .subquery()
    )
    stmt = (
        select(Supplier, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.supplier_id == Supplier.id)
        .where(Supplier.org_id == org_id)
    )
    if not include_inactive:
        stmt = stmt.where(Supplier.is_active.is_(True))
    if q:
        stmt = stmt.where(Supplier.name.ilike(f"%{q.replace('%', '').replace('_', '')[:100]}%"))
    return [supplier_out(s, int(n)) for s, n in db.execute(stmt.order_by(func.lower(Supplier.name))).all()]


def _name_taken(exc: IntegrityError) -> bool:
    return "uq_suppliers_org_name" in str(exc.orig)


def create_supplier(db: Session, org_id: uuid.UUID, values: dict) -> Supplier:
    s = Supplier(org_id=org_id, **values)
    db.add(s)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        if _name_taken(exc):
            raise Conflict("A supplier with this name already exists", code="supplier_exists") from exc
        raise
    return s


def update_supplier(db: Session, org_id: uuid.UUID, supplier_id: uuid.UUID, changes: dict) -> Supplier:
    expected = changes.pop("expected_version")
    s = db.scalar(
        select(Supplier)
        .where(Supplier.id == supplier_id, Supplier.org_id == org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if s is None:
        raise NotFound("Supplier not found")
    if s.version != expected:
        raise Conflict("The supplier was modified by someone else. Reload and try again.")
    for k, v in changes.items():
        setattr(s, k, v)
    s.version += 1
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        if _name_taken(exc):
            raise Conflict("A supplier with this name already exists", code="supplier_exists") from exc
        raise
    return s


def assign_products(db: Session, org_id: uuid.UUID, supplier_id: uuid.UUID | None, product_ids: list[uuid.UUID]) -> int:
    """Set (or clear, with ``supplier_id=None``) the supplier of several products. Returns how many changed."""
    if supplier_id is not None:
        get_supplier(db, org_id, supplier_id)
    products = list(db.scalars(select(Product).where(Product.org_id == org_id, Product.id.in_(product_ids))))
    if len(products) != len(set(product_ids)):
        raise NotFound("One or more products were not found")
    changed = 0
    for p in products:
        if p.supplier_id != supplier_id:
            p.supplier_id = supplier_id
            p.version += 1
            changed += 1
    db.flush()
    return changed


# --- purchase orders: reading -----------------------------------------------------------------------------------------


def _lock_order(db: Session, org_id: uuid.UUID, po_id: uuid.UUID) -> PurchaseOrder:
    po = db.scalar(
        select(PurchaseOrder)
        .where(PurchaseOrder.id == po_id, PurchaseOrder.org_id == org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if po is None:
        raise NotFound("Purchase order not found")
    return po


def get_order(db: Session, org_id: uuid.UUID, po_id: uuid.UUID) -> PurchaseOrder:
    po = db.get(PurchaseOrder, po_id)
    if po is None or po.org_id != org_id:
        raise NotFound("Purchase order not found")
    return po


def _lines(db: Session, po_id: uuid.UUID) -> list[tuple[PurchaseOrderLine, Product]]:
    return list(
        db.execute(
            select(PurchaseOrderLine, Product)
            .join(Product, Product.id == PurchaseOrderLine.product_id)
            .where(PurchaseOrderLine.purchase_order_id == po_id)
            .order_by(Product.sku)
        )
        .tuples()
        .all()
    )


def order_number(n: int) -> str:
    return f"PO-{n:06d}"


def order_out(db: Session, po: PurchaseOrder) -> dict:
    lines = _lines(db, po.id)
    supplier = db.get(Supplier, po.supplier_id) if po.supplier_id else None
    total = 0.0
    total_known = True
    out_lines = []
    for line, product in lines:
        value = None if line.unit_cost is None else round(line.quantity * line.unit_cost, 2)
        if value is None:
            total_known = False
        else:
            total += value
        out_lines.append(
            {
                "id": str(line.id),
                "product_id": str(product.id),
                "sku": product.sku,
                "name": product.name,
                "quantity": line.quantity,
                "unit_cost": line.unit_cost,
                "line_value": value,
                "received_qty": line.received_qty,
                "outstanding_qty": line.quantity - line.received_qty,
                "suggested_qty": line.suggested_qty,
                "suggestion": line.suggestion,
                "pack_size": product.pack_size,
                "min_order_qty": product.min_order_qty,
            }
        )
    warnings = []
    if supplier is not None and supplier.min_order_value and total_known and total < supplier.min_order_value:
        warnings.append(
            f"Order value {total:,.2f} is below {supplier.name}'s minimum of {supplier.min_order_value:,.2f}"
        )
    if not total_known:
        warnings.append("Some lines have no unit cost, so the order total is incomplete")
    for item in out_lines:
        if item["pack_size"] > 1 and item["quantity"] % item["pack_size"]:
            warnings.append(f"{item['sku']}: {item['quantity']} is not a multiple of the pack size {item['pack_size']}")
        if item["min_order_qty"] and item["quantity"] < item["min_order_qty"]:
            warnings.append(f"{item['sku']}: {item['quantity']} is below the minimum order of {item['min_order_qty']}")
    return {
        "id": str(po.id),
        "number": order_number(po.number),
        "status": po.status,
        "supplier_id": str(po.supplier_id) if po.supplier_id else None,
        "supplier_name": po.supplier_name or (supplier.name if supplier else None),
        "supplier_email": supplier.email if supplier else None,
        "expected_date": po.expected_date.isoformat() if po.expected_date else None,
        "notes": po.notes,
        "source": po.source,
        "created_by": str(po.created_by) if po.created_by else None,
        "approved_by": str(po.approved_by) if po.approved_by else None,
        "created_at": po.created_at.isoformat() if po.created_at else None,
        "approved_at": po.approved_at.isoformat() if po.approved_at else None,
        "sent_at": po.sent_at.isoformat() if po.sent_at else None,
        "received_at": po.received_at.isoformat() if po.received_at else None,
        "cancelled_at": po.cancelled_at.isoformat() if po.cancelled_at else None,
        "version": po.version,
        "line_count": len(out_lines),
        "total_units": sum(i["quantity"] for i in out_lines),
        "total_value": round(total, 2) if out_lines else 0.0,
        "total_complete": total_known,
        "lines": out_lines,
        "warnings": warnings,
    }


def list_orders(
    db: Session,
    org_id: uuid.UUID,
    *,
    status: str | None = None,
    supplier_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    agg = (
        select(
            PurchaseOrderLine.purchase_order_id.label("po_id"),
            func.count().label("lines"),
            func.sum(PurchaseOrderLine.quantity).label("units"),
            func.sum(PurchaseOrderLine.quantity * PurchaseOrderLine.unit_cost).label("value"),
            func.count().filter(PurchaseOrderLine.unit_cost.is_(None)).label("no_cost"),
        )
        .where(PurchaseOrderLine.org_id == org_id)
        .group_by(PurchaseOrderLine.purchase_order_id)
        .subquery()
    )
    stmt = (
        select(PurchaseOrder, Supplier.name, agg.c.lines, agg.c.units, agg.c.value, agg.c.no_cost)
        .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .outerjoin(agg, agg.c.po_id == PurchaseOrder.id)
        .where(PurchaseOrder.org_id == org_id)
    )
    if status == "open":
        stmt = stmt.where(PurchaseOrder.status.in_(OPEN))
    elif status:
        stmt = stmt.where(PurchaseOrder.status == status)
    if supplier_id:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(PurchaseOrder.number.desc()).limit(limit).offset(offset)).all()
    items = [
        {
            "id": str(po.id),
            "number": order_number(po.number),
            "status": po.status,
            "supplier_id": str(po.supplier_id) if po.supplier_id else None,
            "supplier_name": po.supplier_name or sname,
            "expected_date": po.expected_date.isoformat() if po.expected_date else None,
            "source": po.source,
            "created_at": po.created_at.isoformat() if po.created_at else None,
            "line_count": int(lines or 0),
            "total_units": int(units or 0),
            "total_value": round(float(value or 0.0), 2),
            "total_complete": not no_cost,
        }
        for po, sname, lines, units, value, no_cost in rows
    ]
    return items, total


# --- purchase orders: changes -----------------------------------------------------------------------------------------


def _next_number(db: Session, org_id: uuid.UUID) -> int:
    # Serialise numbering per organisation on its row; the unique constraint is the backstop.
    db.execute(select(Organization.id).where(Organization.id == org_id).with_for_update())
    return int(db.scalar(select(func.max(PurchaseOrder.number)).where(PurchaseOrder.org_id == org_id)) or 0) + 1


def _set_lines(db: Session, org_id: uuid.UUID, po: PurchaseOrder, lines: list[dict]) -> None:
    if len(lines) > MAX_LINES:
        raise ValidationFailed(f"An order can have at most {MAX_LINES} lines")
    ids = [ln["product_id"] for ln in lines]
    if len(ids) != len(set(ids)):
        raise ValidationFailed("Each product can appear only once in an order")
    products = {p.id: p for p in db.scalars(select(Product).where(Product.org_id == org_id, Product.id.in_(ids)))}
    missing = [str(i) for i in ids if i not in products]
    if missing:
        raise NotFound("One or more products were not found", details={"product_ids": missing})
    existing = {
        ln.product_id: ln
        for ln in db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id == po.id))
    }
    keep = set()
    for ln in lines:
        pid = ln["product_id"]
        keep.add(pid)
        cost = ln.get("unit_cost")
        if cost is None and "unit_cost" not in ln:
            cost = products[pid].unit_cost
        row = existing.get(pid)
        if row is None:
            row = PurchaseOrderLine(org_id=org_id, purchase_order_id=po.id, product_id=pid)
            db.add(row)
        row.quantity = int(ln["quantity"])
        row.unit_cost = cost
        if "suggested_qty" in ln:
            row.suggested_qty = ln["suggested_qty"]
            row.suggestion = ln.get("suggestion")
    for pid, row in existing.items():
        if pid not in keep:
            db.delete(row)


def create_order(
    db: Session,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    supplier_id: uuid.UUID | None,
    lines: list[dict],
    expected_date: date | None = None,
    notes: str | None = None,
    source: str = "manual",
) -> PurchaseOrder:
    if supplier_id is not None:
        s = get_supplier(db, org_id, supplier_id)
        if not s.is_active:
            raise ValidationFailed("This supplier is inactive")
    po = PurchaseOrder(
        org_id=org_id,
        number=_next_number(db, org_id),
        status="draft",
        supplier_id=supplier_id,
        expected_date=expected_date,
        notes=notes,
        source=source,
        created_by=user_id,
    )
    db.add(po)
    db.flush()
    _set_lines(db, org_id, po, lines)
    db.flush()
    return po


def update_draft(db: Session, org_id: uuid.UUID, po_id: uuid.UUID, changes: dict) -> PurchaseOrder:
    expected = changes.pop("expected_version")
    po = _lock_order(db, org_id, po_id)
    if po.version != expected:
        raise Conflict("The order was modified by someone else. Reload and try again.")
    if po.status != "draft":
        raise Conflict(f"Only draft orders can be edited; this one is {po.status}", code="not_draft")
    if "supplier_id" in changes:
        sid = changes.pop("supplier_id")
        if sid is not None and not get_supplier(db, org_id, sid).is_active:
            raise ValidationFailed("This supplier is inactive")
        po.supplier_id = sid
    if "lines" in changes:
        _set_lines(db, org_id, po, changes.pop("lines"))
    for k in ("expected_date", "notes"):
        if k in changes:
            setattr(po, k, changes[k])
    po.version += 1
    db.flush()
    return po


def _check_version(po: PurchaseOrder, expected: int | None) -> None:
    if expected is not None and po.version != expected:
        raise Conflict("The order was changed by someone else. Reload it and check before trying again.")


def _transition(po: PurchaseOrder, allowed: tuple[str, ...], action: str) -> None:
    if po.status not in allowed:
        raise Conflict(f"Cannot {action} an order that is {po.status.replace('_', ' ')}", code="invalid_transition")


def _lock_products(db: Session, org_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, Product]:
    return {
        p.id: p
        for p in db.scalars(
            select(Product)
            .where(Product.org_id == org_id, Product.id.in_(ids))
            .order_by(Product.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }


def approve(
    db: Session, org_id: uuid.UUID, po_id: uuid.UUID, user_id: uuid.UUID, *, expected_version: int | None = None
) -> PurchaseOrder:
    po = _lock_order(db, org_id, po_id)
    _check_version(po, expected_version)
    _transition(po, ("draft",), "approve")
    if po.supplier_id is None:
        raise ValidationFailed("Choose a supplier before approving")
    if not _lines(db, po.id):
        raise ValidationFailed("An order needs at least one line")
    po.status, po.approved_by, po.approved_at = "approved", user_id, _now()
    po.version += 1
    db.flush()
    return po


def send(db: Session, org_id: uuid.UUID, po_id: uuid.UUID, *, expected_version: int | None = None) -> PurchaseOrder:
    """Mark as sent to the supplier (Aurora sends no e-mail): the quantities become stock on order."""
    po = _lock_order(db, org_id, po_id)
    _check_version(po, expected_version)
    _transition(po, ("approved",), "send")
    supplier = get_supplier(db, org_id, po.supplier_id) if po.supplier_id else None
    lines = [ln for ln, _ in _lines(db, po.id)]
    products = _lock_products(db, org_id, [ln.product_id for ln in lines])
    for ln in lines:
        p = products[ln.product_id]
        p.on_order += ln.quantity
        p.version += 1
    po.status, po.sent_at = "sent", _now()
    po.supplier_name = supplier.name if supplier else po.supplier_name
    if po.expected_date is None and supplier is not None and supplier.lead_time_days:
        po.expected_date = (po.sent_at + timedelta(days=supplier.lead_time_days)).date()
    po.version += 1
    db.flush()
    return po


def receive(
    db: Session,
    org_id: uuid.UUID,
    po_id: uuid.UUID,
    received: list[dict] | None,
    *,
    expected_version: int | None = None,
) -> PurchaseOrder:
    """Book goods in. ``received`` = [{product_id, quantity}]; None receives everything still outstanding."""
    po = _lock_order(db, org_id, po_id)
    _check_version(po, expected_version)
    _transition(po, ("sent", "partially_received"), "receive")
    lines = {ln.product_id: ln for ln, _ in _lines(db, po.id)}
    if received is None:
        todo = {pid: ln.quantity - ln.received_qty for pid, ln in lines.items() if ln.quantity > ln.received_qty}
    else:
        todo = defaultdict(int)
        for r in received:
            todo[r["product_id"]] += int(r["quantity"])
    if not todo:
        raise ValidationFailed("Nothing to receive")
    for pid, qty in todo.items():
        ln = lines.get(pid)
        if ln is None:
            raise ValidationFailed("A received product is not on this order", details={"product_id": str(pid)})
        if qty <= 0 or qty > ln.quantity - ln.received_qty:
            raise ValidationFailed(
                f"Received quantity must be between 1 and the {ln.quantity - ln.received_qty} still outstanding",
                details={"product_id": str(pid)},
            )
    products = _lock_products(db, org_id, list(todo))
    for pid, qty in todo.items():
        ln, p = lines[pid], products[pid]
        ln.received_qty += qty
        p.on_hand += qty
        p.on_order = max(p.on_order - qty, 0)
        p.inventory_known = True
        p.version += 1
    complete = all(ln.received_qty == ln.quantity for ln in lines.values())
    po.status = "received" if complete else "partially_received"
    if complete:
        po.received_at = _now()
    po.version += 1
    db.flush()
    return po


def cancel(db: Session, org_id: uuid.UUID, po_id: uuid.UUID, *, expected_version: int | None = None) -> PurchaseOrder:
    po = _lock_order(db, org_id, po_id)
    _check_version(po, expected_version)
    _transition(po, OPEN, "cancel")
    if po.status in ("sent", "partially_received"):
        lines = [ln for ln, _ in _lines(db, po.id)]
        products = _lock_products(db, org_id, [ln.product_id for ln in lines])
        for ln in lines:
            outstanding = ln.quantity - ln.received_qty
            if outstanding:
                p = products[ln.product_id]
                p.on_order = max(p.on_order - outstanding, 0)
                p.version += 1
    po.status, po.cancelled_at = "cancelled", _now()
    po.version += 1
    db.flush()
    return po


def delete_draft(db: Session, org_id: uuid.UUID, po_id: uuid.UUID, *, expected_version: int | None = None) -> None:
    po = _lock_order(db, org_id, po_id)
    _check_version(po, expected_version)
    if po.status != "draft":
        raise Conflict("Only drafts can be deleted; cancel the order instead", code="not_draft")
    db.delete(po)
    db.flush()


# --- drafts from reorder suggestions ----------------------------------------------------------------------------------


def suggest(db: Session, org_id: uuid.UUID, user_id: uuid.UUID, product_ids: list[uuid.UUID] | None = None) -> dict:
    """Create one draft per supplier from the current reorder suggestions.

    Products already on a draft or approved order are skipped (stock that is sent is already counted in on_order,
    which the suggestion accounts for). Products without a supplier go on one draft with no supplier, to be assigned.
    """
    busy = set(
        db.scalars(
            select(PurchaseOrderLine.product_id)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .where(PurchaseOrder.org_id == org_id, PurchaseOrder.status.in_(OPEN_UNSENT))
        )
    )
    eff = settings_service.for_org(db, org_id)
    recs = inventory_service.risk_matrix(db, org_id, product_ids=product_ids, org_settings=eff)
    groups: dict[uuid.UUID | None, list[dict]] = defaultdict(list)
    skipped = {"already_on_order": 0, "no_reorder_needed": 0, "no_data": 0}
    suppliers = {
        p.id: p.supplier_id
        for p in db.scalars(select(Product).where(Product.org_id == org_id, Product.supplier_id.is_not(None)))
    }
    for r in recs:
        pid = uuid.UUID(r["product_id"])
        if r.get("status") != "ok":
            skipped["no_data"] += 1
            continue
        if not r.get("recommended_order_qty"):
            skipped["no_reorder_needed"] += 1
            continue
        if pid in busy:
            skipped["already_on_order"] += 1
            continue
        qty = int(r["recommended_order_qty"])
        groups[suppliers.get(pid)].append(
            {
                "product_id": pid,
                "quantity": qty,
                "suggested_qty": qty,
                "suggestion": {
                    "risk_level": r.get("risk_level"),
                    "days_until_stockout": r.get("days_until_stockout"),
                    "reorder_point": r.get("reorder_point"),
                    "inventory_position": r.get("inventory_position"),
                    "forecast_method": r.get("forecast_method"),
                    "forecast_as_of": r.get("forecast_as_of"),
                    "overrides_applied": len(r.get("overrides_applied") or []),
                },
            }
        )
    created = []
    for supplier_id, lines in sorted(groups.items(), key=lambda kv: (kv[0] is None, str(kv[0]))):
        po = create_order(
            db,
            org_id,
            user_id,
            supplier_id=supplier_id,
            lines=lines,
            source="suggested",
            notes="Drafted from reorder suggestions.",
        )
        created.append(po)
    return {"created": created, "skipped": skipped}


# --- export -----------------------------------------------------------------------------------------------------------


def _safe_cell(v: object) -> str:
    """Neutralise spreadsheet formulas in exported text (CSV injection)."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def export_csv(db: Session, po: PurchaseOrder) -> str:
    o = order_out(db, po)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["order", o["number"]])
    w.writerow(["supplier", _safe_cell(o["supplier_name"])])
    w.writerow(["status", o["status"]])
    w.writerow(["expected_date", o["expected_date"] or ""])
    w.writerow([])
    w.writerow(["sku", "name", "quantity", "unit_cost", "line_value", "received_qty"])
    for ln in o["lines"]:
        w.writerow(
            [
                _safe_cell(ln["sku"]),
                _safe_cell(ln["name"]),
                ln["quantity"],
                "" if ln["unit_cost"] is None else ln["unit_cost"],
                "" if ln["line_value"] is None else ln["line_value"],
                ln["received_qty"],
            ]
        )
    w.writerow([])
    w.writerow(["total_units", o["total_units"]])
    w.writerow(["total_value", o["total_value"] if o["total_complete"] else f"{o['total_value']} (incomplete)"])
    return buf.getvalue()
