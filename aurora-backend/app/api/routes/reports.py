"""PDF documents and saved views (ADR-047)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import Principal, require
from app.core import rate_limit
from app.core.security import Perm
from app.db.session import get_db
from app.services import audit, pdf_reports, purchasing_service, saved_views

router = APIRouter(tags=["reports"])

Page = Literal["products", "inventory", "purchase_orders", "alerts"]


def _pdf(data: bytes, filename: str) -> Response:
    return Response(
        data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/purchase-orders/{po_id}/export.pdf")
def purchase_order_pdf(
    po_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> Response:
    rate_limit.hit(f"pdf:{p.user_id}", 30, 60)
    po = purchasing_service.get_order(db, p.org_id, po_id)
    data = pdf_reports.purchase_order_pdf(db, p.org_id, po, font_dir=pdf_reports.unicode_font())
    return _pdf(data, f"{purchasing_service.order_number(po.number)}.pdf")


@router.get("/reports/inventory.pdf")
def inventory_pdf(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> Response:
    rate_limit.hit(f"pdf-inventory:{p.user_id}", 5, 60)  # scores every product
    data = pdf_reports.inventory_report_pdf(db, p.org_id, font_dir=pdf_reports.unicode_font())
    db.commit()  # forecasts generated on the way are stored, as for recommendations
    return _pdf(data, "aurora-inventory-report.pdf")


class ViewIn(BaseModel):
    page: Page
    name: str = Field(min_length=1, max_length=80)
    params: dict[str, str | bool] = Field(default_factory=dict, max_length=10)
    shared: bool = False


@router.get("/saved-views")
def list_views(
    page: Page = Query(...), p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> list[dict]:
    return [saved_views.out(v, p) for v in saved_views.list_for(db, p, page)]


@router.post("/saved-views", status_code=201)
def create_view(body: ViewIn, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> dict:
    v = saved_views.create(db, p, **body.model_dump())
    if v.shared:  # shared views change what colleagues see, so they are audited
        audit.record_for(
            db,
            p,
            "saved_view.shared",
            resource_type="saved_view",
            resource_id=v.id,
            details={"page": v.page, "name": v.name},
        )
    db.commit()
    return saved_views.out(v, p)


@router.delete("/saved-views/{view_id}", status_code=204)
def delete_view(view_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> None:
    v = saved_views.delete(db, p, view_id)
    if v.shared:
        audit.record_for(
            db,
            p,
            "saved_view.deleted",
            resource_type="saved_view",
            resource_id=v.id,
            details={"page": v.page, "name": v.name},
        )
    db.commit()
