"""Promotions: plan, estimate and measure (ADR-045)."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import Principal, require
from app.core.security import Perm
from app.db.session import get_db
from app.services import audit, promotion_service

router = APIRouter(prefix="/promotions", tags=["promotions"])

MAX = promotion_service.MAX_PRODUCTS


class PromotionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX)
    category: str | None = Field(default=None, max_length=200)
    discount_pct: float | None = Field(default=None, ge=0, le=95)
    uplift: float | None = Field(default=None, gt=0, le=promotion_service.MAX_UPLIFT)
    use_estimate: bool = True
    notes: str = Field(default="", max_length=500)


class PromotionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    product_ids: list[uuid.UUID] | None = Field(default=None, max_length=MAX)
    category: str | None = Field(default=None, max_length=200)
    discount_pct: float | None = Field(default=None, ge=0, le=95)
    uplift: float | None = Field(default=None, gt=0, le=promotion_service.MAX_UPLIFT)
    notes: str | None = Field(default=None, max_length=500)
    expected_version: int | None = None


class EstimateIn(BaseModel):
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX)
    category: str | None = Field(default=None, max_length=200)
    discount_pct: float | None = Field(default=None, ge=0, le=95)


def _details(pr) -> dict:  # type: ignore[no-untyped-def]
    return {
        "name": pr.name,
        "start_date": pr.start_date.isoformat(),
        "end_date": pr.end_date.isoformat(),
        "products": len(pr.product_ids),
        "uplift": pr.uplift,
        "uplift_source": pr.uplift_source,
    }


@router.get("")
def list_promotions(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> list[dict]:
    return [promotion_service.out(db, p.org_id, x) for x in promotion_service.list_for_org(db, p.org_id)]


@router.post("/estimate")
def estimate(body: EstimateIn, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    ids = promotion_service._resolve_products(db, p.org_id, body.product_ids, body.category)
    return promotion_service.estimate(db, p.org_id, ids, body.discount_pct)


@router.post("", status_code=201)
def create_promotion(
    body: PromotionIn, p: Principal = Depends(require(Perm.manage_overrides)), db: Session = Depends(get_db)
) -> dict:
    pr = promotion_service.create(db, p.org_id, p.user_id, body.model_dump())
    audit.record_for(db, p, "promotion.created", resource_type="promotion", resource_id=pr.id, details=_details(pr))
    db.commit()
    return promotion_service.out(db, p.org_id, pr, detail=True)


@router.get("/{promo_id}")
def get_promotion(
    promo_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> dict:
    return promotion_service.out(db, p.org_id, promotion_service.get(db, p.org_id, promo_id), detail=True)


@router.patch("/{promo_id}")
def update_promotion(
    promo_id: uuid.UUID,
    body: PromotionPatch,
    p: Principal = Depends(require(Perm.manage_overrides)),
    db: Session = Depends(get_db),
) -> dict:
    pr = promotion_service.update(db, p.org_id, promo_id, body.model_dump(exclude_unset=True))
    audit.record_for(db, p, "promotion.updated", resource_type="promotion", resource_id=pr.id, details=_details(pr))
    db.commit()
    return promotion_service.out(db, p.org_id, pr, detail=True)


@router.delete("/{promo_id}", status_code=204)
def delete_promotion(
    promo_id: uuid.UUID, p: Principal = Depends(require(Perm.manage_overrides)), db: Session = Depends(get_db)
) -> None:
    pr = promotion_service.delete(db, p.org_id, promo_id)
    audit.record_for(db, p, "promotion.deleted", resource_type="promotion", resource_id=promo_id, details=_details(pr))
    db.commit()
