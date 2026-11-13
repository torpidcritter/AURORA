"""Stock locations, stock by location and transfers between locations."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Principal, require
from app.core.security import Perm
from app.db.session import get_db
from app.schemas.common import Page
from app.schemas.purchasing import LocationIn, LocationUpdate, TransferIn
from app.services import audit, stock_service
from app.services.product_service import get_product

router = APIRouter(tags=["locations"])


@router.get("/locations")
def list_locations(
    include_inactive: bool = False, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> list[dict]:
    """Locations with the units and value of stock each holds. The default location is created on first use."""
    out = stock_service.list_locations(db, p.org_id, include_inactive)
    db.commit()  # keeps a newly created default location
    return out


@router.post("/locations", status_code=201)
def create_location(
    body: LocationIn, p: Principal = Depends(require(Perm.manage_settings)), db: Session = Depends(get_db)
) -> dict:
    loc = stock_service.create_location(db, p.org_id, body.model_dump())
    audit.record_for(
        db, p, "location.created", resource_type="location", resource_id=loc.id, details={"code": loc.code}
    )
    db.commit()
    return stock_service.location_out(loc)


@router.patch("/locations/{location_id}")
def update_location(
    location_id: uuid.UUID,
    body: LocationUpdate,
    p: Principal = Depends(require(Perm.manage_settings)),
    db: Session = Depends(get_db),
) -> dict:
    changes = body.model_dump(exclude_unset=True)
    loc = stock_service.update_location(db, p.org_id, location_id, dict(changes))
    changes.pop("expected_version", None)
    audit.record_for(
        db, p, "location.updated", resource_type="location", resource_id=loc.id, details={"changed": sorted(changes)}
    )
    db.commit()
    rows = [r for r in stock_service.list_locations(db, p.org_id, True) if r["id"] == str(loc.id)]
    return rows[0]


@router.get("/products/{product_id}/stock")
def product_stock(
    product_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> dict:
    """On-hand and on-order units of one product at each location; the totals are the product's figures."""
    product = get_product(db, p.org_id, product_id)
    levels = stock_service.levels_for_product(db, p.org_id, product_id)
    db.commit()
    return {
        "product_id": str(product.id),
        "on_hand": product.on_hand,
        "on_order": product.on_order,
        "inventory_known": product.inventory_known,
        "locations": levels,
    }


@router.get("/stock/transfers", response_model=Page[dict])
def list_transfers(
    product_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require(Perm.read)),
    db: Session = Depends(get_db),
) -> Page[dict]:
    items, total = stock_service.list_transfers(db, p.org_id, product_id=product_id, limit=limit, offset=offset)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/stock/transfers", status_code=201)
def create_transfer(
    body: TransferIn, p: Principal = Depends(require(Perm.manage_inventory)), db: Session = Depends(get_db)
) -> dict:
    """Move units between two locations now. Totals do not change; the move is recorded and audited."""
    t = stock_service.transfer(db, p.org_id, p.user_id, **body.model_dump())
    audit.record_for(
        db,
        p,
        "stock.transferred",
        resource_type="product",
        resource_id=t.product_id,
        details={
            "transfer_id": str(t.id),
            "from": str(t.from_location_id),
            "to": str(t.to_location_id),
            "quantity": t.quantity,
        },
    )
    db.commit()
    items, _ = stock_service.list_transfers(db, p.org_id, product_id=t.product_id, limit=1)
    return items[0]
