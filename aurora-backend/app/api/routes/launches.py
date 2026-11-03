"""Launch plans for new products (ADR-044)."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, require
from app.core.security import Perm
from app.db.models import Product, ProductLaunch
from app.db.session import get_db
from app.services import audit, launch_service

router = APIRouter(tags=["launches"])


class LaunchIn(BaseModel):
    launch_date: date
    analogue_ids: list[uuid.UUID] = Field(min_length=1, max_length=launch_service.MAX_ANALOGUES)
    scale: float = Field(default=1.0, gt=0, le=launch_service.MAX_SCALE)
    notes: str = Field(default="", max_length=500)
    expected_version: int | None = None


@router.get("/launches")
def list_launches(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(ProductLaunch, Product.sku, Product.name)
        .join(Product, Product.id == ProductLaunch.product_id)
        .where(ProductLaunch.org_id == p.org_id)
        .order_by(ProductLaunch.launch_date.desc())
    ).all()
    out = []
    for plan, sku, name in rows:
        product = db.get(Product, plan.product_id)
        own = len(launch_service.own_history(db, p.org_id, product, plan.launch_date))  # type: ignore[arg-type]
        out.append(
            {
                **launch_service.plan_out(plan),
                "sku": sku,
                "name": name,
                "own_days": own,
                "active": own < launch_service.HANDOVER_DAYS,
            }
        )
    return out


@router.get("/products/{product_id}/launch")
def get_launch(
    product_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> dict:
    if launch_service.get_plan(db, p.org_id, product_id) is None:
        launch_service.get_product(db, p.org_id, product_id)  # 404 for another organisation's product
        return {"plan": None}
    return launch_service.preview(db, p.org_id, product_id)


@router.get("/products/{product_id}/launch/analogues")
def suggest(product_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> list:
    return launch_service.suggest_analogues(db, p.org_id, product_id)


@router.put("/products/{product_id}/launch")
def save_launch(
    product_id: uuid.UUID,
    body: LaunchIn,
    p: Principal = Depends(require(Perm.manage_overrides)),
    db: Session = Depends(get_db),
) -> dict:
    plan = launch_service.save(
        db,
        p.org_id,
        product_id,
        p.user_id,
        launch_date=body.launch_date,
        analogue_ids=body.analogue_ids,
        scale=body.scale,
        notes=body.notes,
        expected_version=body.expected_version,
    )
    audit.record_for(
        db,
        p,
        "launch.saved",
        resource_type="product",
        resource_id=product_id,
        details={"launch_date": plan.launch_date.isoformat(), "analogues": plan.analogue_ids, "scale": plan.scale},
    )
    db.commit()
    return launch_service.preview(db, p.org_id, product_id)


@router.delete("/products/{product_id}/launch", status_code=204)
def delete_launch(
    product_id: uuid.UUID, p: Principal = Depends(require(Perm.manage_overrides)), db: Session = Depends(get_db)
) -> None:
    launch_service.delete(db, p.org_id, product_id)
    audit.record_for(db, p, "launch.deleted", resource_type="product", resource_id=product_id)
    db.commit()
