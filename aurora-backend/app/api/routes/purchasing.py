"""Suppliers, purchase orders, planner forecast overrides and the overstock report."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import Principal, require
from app.core import rate_limit
from app.core.errors import Conflict, ValidationFailed
from app.core.security import Perm
from app.db.models import PO_STATUSES
from app.db.session import get_db
from app.domain import pricing
from app.schemas.common import Page
from app.schemas.purchasing import (
    AssignProductsIn,
    BudgetPlanIn,
    OverrideIn,
    POCreate,
    POReceive,
    POUpdate,
    POVersion,
    PriceBreaksIn,
    SuggestIn,
    SupplierIn,
    SupplierUpdate,
)
from app.services import (
    audit,
    budget_service,
    override_service,
    overstock_service,
    purchasing_service,
    stock_service,
    supplier_performance,
)
from app.services.purchasing_service import order_number

router = APIRouter(tags=["purchasing"])

POStatusFilter = Literal["open", *PO_STATUSES]  # type: ignore[valid-type]


# --- suppliers --------------------------------------------------------------------------------------------------------


@router.get("/suppliers")
def list_suppliers(
    q: str | None = Query(None, max_length=100),
    include_inactive: bool = False,
    p: Principal = Depends(require(Perm.read)),
    db: Session = Depends(get_db),
) -> list[dict]:
    return purchasing_service.list_suppliers(db, p.org_id, q=q, include_inactive=include_inactive)


@router.post("/suppliers", status_code=201)
def create_supplier(
    body: SupplierIn, p: Principal = Depends(require(Perm.manage_purchasing)), db: Session = Depends(get_db)
) -> dict:
    s = purchasing_service.create_supplier(db, p.org_id, body.model_dump())
    audit.record_for(db, p, "supplier.created", resource_type="supplier", resource_id=s.id, details={"name": s.name})
    db.commit()
    return purchasing_service.supplier_out(s, 0)


@router.get("/suppliers/performance")
def supplier_performance_all(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> list[dict]:
    """Measured lead time, on-time delivery and fill rate per supplier over the last year (ADR-038)."""
    stats = supplier_performance.for_org(db, p.org_id)
    return [{"supplier_id": str(sid), **v} for sid, v in stats.items()]


@router.put("/products/{product_id}/price-breaks")
def set_price_breaks(
    product_id: uuid.UUID,
    body: PriceBreaksIn,
    p: Principal = Depends(require(Perm.manage_purchasing)),
    db: Session = Depends(get_db),
) -> dict:
    prod = stock_service.lock_product(db, p.org_id, product_id)
    if prod.version != body.expected_version:
        raise Conflict("The product was modified by someone else. Reload and try again.")
    try:
        breaks = pricing.normalise([b.model_dump() for b in body.breaks], prod.unit_cost)
    except pricing.PriceBreakError as exc:
        raise ValidationFailed(str(exc)) from exc
    prod.price_breaks = breaks or None
    prod.version += 1
    audit.record_for(
        db, p, "product.price_breaks", resource_type="product", resource_id=prod.id, details={"breaks": breaks}
    )
    db.commit()
    return {"product_id": str(prod.id), "price_breaks": breaks, "version": prod.version}


@router.get("/suppliers/{supplier_id}")
def get_supplier(
    supplier_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> dict:
    s = purchasing_service.get_supplier(db, p.org_id, supplier_id)
    rows = [r for r in purchasing_service.list_suppliers(db, p.org_id, include_inactive=True) if r["id"] == str(s.id)]
    return rows[0]


@router.patch("/suppliers/{supplier_id}")
def update_supplier(
    supplier_id: uuid.UUID,
    body: SupplierUpdate,
    p: Principal = Depends(require(Perm.manage_purchasing)),
    db: Session = Depends(get_db),
) -> dict:
    changes = body.model_dump(exclude_unset=True)
    s = purchasing_service.update_supplier(db, p.org_id, supplier_id, dict(changes))
    changes.pop("expected_version", None)
    audit.record_for(
        db, p, "supplier.updated", resource_type="supplier", resource_id=s.id, details={"changed": sorted(changes)}
    )
    db.commit()
    return get_supplier(supplier_id, p, db)


@router.post("/suppliers/assign")
def assign_products(
    body: AssignProductsIn, p: Principal = Depends(require(Perm.manage_purchasing)), db: Session = Depends(get_db)
) -> dict:
    """Set the supplier of several products at once (null removes it)."""
    changed = purchasing_service.assign_products(db, p.org_id, body.supplier_id, body.product_ids)
    audit.record_for(
        db,
        p,
        "supplier.products_assigned",
        resource_type="supplier",
        resource_id=body.supplier_id,
        details={"products": len(body.product_ids), "changed": changed},
    )
    db.commit()
    return {"changed": changed}


# --- purchase orders --------------------------------------------------------------------------------------------------


@router.get("/purchase-orders", response_model=Page[dict])
def list_orders(
    status: POStatusFilter | None = None,
    supplier_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require(Perm.read)),
    db: Session = Depends(get_db),
) -> Page[dict]:
    items, total = purchasing_service.list_orders(
        db, p.org_id, status=status, supplier_id=supplier_id, limit=limit, offset=offset
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/purchase-orders", status_code=201)
def create_order(
    body: POCreate, p: Principal = Depends(require(Perm.manage_purchasing)), db: Session = Depends(get_db)
) -> dict:
    po = purchasing_service.create_order(
        db,
        p.org_id,
        p.user_id,
        supplier_id=body.supplier_id,
        location_id=body.location_id,
        lines=[ln.model_dump(exclude_unset=True) for ln in body.lines],
        expected_date=body.expected_date,
        notes=body.notes,
    )
    _audit(db, p, "purchase_order.created", po, lines=len(body.lines))
    db.commit()
    return purchasing_service.order_out(db, po)


@router.post("/purchase-orders/budget-plan")
def budget_plan(body: BudgetPlanIn, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    """The best mix of reorders for a budget (ADR-037). Read-only: nothing is drafted until /suggest is called."""
    rate_limit.hit(f"budget-plan:{p.user_id}", 20, 60)
    res = budget_service.plan(db, p.org_id, **body.model_dump())
    db.commit()  # forecasts generated on the way are stored, as for recommendations
    return res


@router.post("/purchase-orders/suggest", status_code=201)
def suggest_orders(
    body: SuggestIn, p: Principal = Depends(require(Perm.manage_purchasing)), db: Session = Depends(get_db)
) -> dict:
    """Draft one order per supplier from the current reorder suggestions. Nothing is approved or sent."""
    res = purchasing_service.suggest(db, p.org_id, p.user_id, body.product_ids, body.quantities)
    for po in res["created"]:
        _audit(db, p, "purchase_order.suggested", po)
    db.commit()
    return {
        "created": [purchasing_service.order_out(db, po) for po in res["created"]],
        "skipped": res["skipped"],
    }


@router.get("/purchase-orders/{po_id}")
def get_order(po_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    return purchasing_service.order_out(db, purchasing_service.get_order(db, p.org_id, po_id))


@router.get("/purchase-orders/{po_id}/export.csv")
def export_order(
    po_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> Response:
    po = purchasing_service.get_order(db, p.org_id, po_id)
    return Response(
        purchasing_service.export_csv(db, po),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{order_number(po.number)}.csv"'},
    )


@router.patch("/purchase-orders/{po_id}")
def update_order(
    po_id: uuid.UUID,
    body: POUpdate,
    p: Principal = Depends(require(Perm.manage_purchasing)),
    db: Session = Depends(get_db),
) -> dict:
    changes = body.model_dump(exclude_unset=True)
    if body.lines is not None:
        changes["lines"] = [ln.model_dump(exclude_unset=True) for ln in body.lines]
    elif "lines" in changes:
        changes.pop("lines")  # null means "leave the lines alone"
    po = purchasing_service.update_draft(db, p.org_id, po_id, dict(changes))
    _audit(db, p, "purchase_order.updated", po, changed=sorted(k for k in changes if k != "expected_version"))
    db.commit()
    return purchasing_service.order_out(db, po)


@router.post("/purchase-orders/{po_id}/approve")
def approve_order(
    po_id: uuid.UUID,
    body: POVersion,
    p: Principal = Depends(require(Perm.approve_purchase_orders)),
    db: Session = Depends(get_db),
) -> dict:
    po = purchasing_service.approve(db, p.org_id, po_id, p.user_id, expected_version=body.expected_version)
    _audit(db, p, "purchase_order.approved", po)
    db.commit()
    return purchasing_service.order_out(db, po)


@router.post("/purchase-orders/{po_id}/send")
def send_order(
    po_id: uuid.UUID,
    body: POVersion,
    p: Principal = Depends(require(Perm.manage_purchasing)),
    db: Session = Depends(get_db),
) -> dict:
    """Record that the order went to the supplier. Aurora does not e-mail it; download the CSV to send it."""
    po = purchasing_service.send(db, p.org_id, po_id, expected_version=body.expected_version)
    _audit(db, p, "purchase_order.sent", po)
    db.commit()
    return purchasing_service.order_out(db, po)


@router.post("/purchase-orders/{po_id}/receive")
def receive_order(
    po_id: uuid.UUID,
    body: POReceive,
    p: Principal = Depends(require(Perm.manage_purchasing)),
    db: Session = Depends(get_db),
) -> dict:
    lines = None if body.lines is None else [ln.model_dump() for ln in body.lines]
    po = purchasing_service.receive(db, p.org_id, po_id, lines, expected_version=body.expected_version)
    _audit(
        db,
        p,
        "purchase_order.received",
        po,
        received="all outstanding" if lines is None else {str(r["product_id"]): r["quantity"] for r in lines},
    )
    db.commit()
    return purchasing_service.order_out(db, po)


@router.post("/purchase-orders/{po_id}/cancel")
def cancel_order(
    po_id: uuid.UUID,
    body: POVersion,
    p: Principal = Depends(require(Perm.manage_purchasing)),
    db: Session = Depends(get_db),
) -> dict:
    po = purchasing_service.cancel(db, p.org_id, po_id, expected_version=body.expected_version)
    _audit(db, p, "purchase_order.cancelled", po)
    db.commit()
    return purchasing_service.order_out(db, po)


@router.delete("/purchase-orders/{po_id}", status_code=204)
def delete_order(
    po_id: uuid.UUID,
    expected_version: int = Query(ge=1),
    p: Principal = Depends(require(Perm.manage_purchasing)),
    db: Session = Depends(get_db),
) -> Response:
    po = purchasing_service.get_order(db, p.org_id, po_id)
    number = order_number(po.number)
    purchasing_service.delete_draft(db, p.org_id, po_id, expected_version=expected_version)
    audit.record_for(
        db, p, "purchase_order.deleted", resource_type="purchase_order", resource_id=po_id, details={"number": number}
    )
    db.commit()
    return Response(status_code=204)


def _audit(db: Session, p: Principal, action: str, po, **details) -> None:  # type: ignore[no-untyped-def]
    audit.record_for(
        db,
        p,
        action,
        resource_type="purchase_order",
        resource_id=po.id,
        details={"number": order_number(po.number), "status": po.status, **details},
    )


# --- forecast overrides -----------------------------------------------------------------------------------------------


@router.get("/forecast-overrides", response_model=Page[dict])
def list_overrides(
    product_id: uuid.UUID | None = None,
    include_archived: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require(Perm.read)),
    db: Session = Depends(get_db),
) -> Page[dict]:
    items, total = override_service.list_overrides(
        db, p.org_id, product_id=product_id, include_archived=include_archived, limit=limit, offset=offset
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/forecast-overrides/value-added")
def override_value_added(
    window_days: int = Query(180, ge=7, le=730),
    p: Principal = Depends(require(Perm.read)),
    db: Session = Depends(get_db),
) -> dict:
    """Did overrides beat the model? Model WAPE minus override WAPE on the days each override covered."""
    return override_service.value_added(db, p.org_id, window_days)


@router.post("/forecast-overrides", status_code=201)
def create_override(
    body: OverrideIn, p: Principal = Depends(require(Perm.manage_overrides)), db: Session = Depends(get_db)
) -> dict:
    o = override_service.create(db, p.org_id, p.user_id, **body.model_dump())
    audit.record_for(
        db,
        p,
        "forecast_override.created",
        resource_type="product",
        resource_id=o.product_id,
        details={
            "override_id": str(o.id),
            "kind": o.kind,
            "value": o.value,
            "start_date": o.start_date.isoformat(),
            "end_date": o.end_date.isoformat(),
        },
    )
    db.commit()
    return override_service.to_out(o)


@router.post("/forecast-overrides/{override_id}/archive")
def archive_override(
    override_id: uuid.UUID, p: Principal = Depends(require(Perm.manage_overrides)), db: Session = Depends(get_db)
) -> dict:
    o = override_service.archive(db, p.org_id, override_id)
    audit.record_for(
        db,
        p,
        "forecast_override.archived",
        resource_type="product",
        resource_id=o.product_id,
        details={"override_id": str(o.id)},
    )
    db.commit()
    return override_service.to_out(o)


# --- overstock --------------------------------------------------------------------------------------------------------


@router.get("/inventory/overstock")
def overstock(
    cover_days: int = Query(180, ge=30, le=730, description="Stock beyond this many days of recent demand is excess"),
    dead_days: int = Query(90, ge=30, le=365, description="No sales for this many days counts as dead stock"),
    category: str | None = Query(None, max_length=200),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require(Perm.read)),
    db: Session = Depends(get_db),
) -> dict:
    return overstock_service.report(
        db, p.org_id, cover_days=cover_days, dead_days=dead_days, category=category, limit=limit, offset=offset
    )
