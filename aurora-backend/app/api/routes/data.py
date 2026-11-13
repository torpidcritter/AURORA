"""Datasets, products and inventory-position endpoints."""

from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Principal, require
from app.core import rate_limit
from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.core.security import Perm
from app.db.models import Dataset
from app.db.session import get_db
from app.schemas.common import Page
from app.schemas.data import DatasetOut, HistoryOut, HistoryPoint, InventoryUpdate, ProductOut, UploadResponse
from app.services import audit, dataset_service, product_service, series

router = APIRouter(tags=["data"])


async def _read_limited(file: UploadFile, p: Principal) -> bytes:
    """Read an uploaded file within the size limit, after the per-user upload rate limit."""
    rate_limit.hit(f"upload:{p.user_id}", get_settings().upload_rate_limit_per_minute, 60)
    limit = get_settings().max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ValidationFailed(f"File exceeds the {limit // (1024 * 1024)} MB limit")
    return data


def _parse_tags(tags: str | None) -> dict[str, str]:
    if not tags:
        return {}
    try:
        parsed = json.loads(tags)
    except json.JSONDecodeError as exc:
        raise ValidationFailed("tags must be a JSON object of string values") from exc
    if not isinstance(parsed, dict) or len(parsed) > 20:
        raise ValidationFailed("tags must be a JSON object with at most 20 keys")
    return {str(k)[:50]: str(v)[:200] for k, v in parsed.items()}


async def _upload(file: UploadFile, tags: str | None, missing_policy: str, p: Principal, db: Session) -> UploadResponse:
    data = await _read_limited(file, p)
    outcome = dataset_service.ingest_upload(
        db,
        org_id=p.org_id,
        user_id=p.user_id,
        filename=file.filename or "upload.csv",
        data=data,
        tags=_parse_tags(tags),
        missing_policy=missing_policy,
    )
    if not outcome.duplicate:
        ds = outcome.dataset
        audit.record_for(
            db,
            p,
            "dataset.upload",
            resource_type="dataset",
            resource_id=ds.id,
            details={"filename": ds.filename, "status": ds.status, "accepted_rows": ds.accepted_rows},
        )
    db.commit()
    return UploadResponse(dataset=DatasetOut.model_validate(outcome.dataset), duplicate=outcome.duplicate)


@router.post("/datasets/upload", response_model=UploadResponse, status_code=201)
async def upload_dataset(
    file: UploadFile = File(...),
    tags: str | None = Form(None, description='JSON object, e.g. {"source": "pos"}'),
    missing_policy: Literal["zero", "interpolate"] = Form("zero"),
    p: Principal = Depends(require(Perm.tag_datasets)),
    db: Session = Depends(get_db),
) -> UploadResponse:
    """Programmatic upload with metadata tags (Analyst+, spec 8.2). Operators use POST /upload-dataset."""
    return await _upload(file, tags, missing_policy, p, db)


@router.post("/upload-dataset", response_model=UploadResponse, status_code=201)
async def upload_dataset_alias(
    file: UploadFile = File(...),
    missing_policy: Literal["zero", "interpolate"] = Form("zero"),
    p: Principal = Depends(require(Perm.upload_data)),
    db: Session = Depends(get_db),
) -> UploadResponse:
    """Multipart CSV/Excel/JSON upload with validation and quality scoring (Operator+, spec 8.2)."""
    return await _upload(file, None, missing_policy, p, db)


@router.get("/datasets", response_model=Page[DatasetOut])
def list_datasets(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require(Perm.read)),
    db: Session = Depends(get_db),
) -> Page[DatasetOut]:
    q = select(Dataset).where(Dataset.org_id == p.org_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Dataset.created_at.desc()).limit(limit).offset(offset)).all()
    return Page(items=[DatasetOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/datasets/{dataset_id}", response_model=DatasetOut)
def get_dataset(
    dataset_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if ds is None or ds.org_id != p.org_id:
        raise NotFound("Dataset not found")
    return ds


@router.get("/products", response_model=Page[ProductOut])
def list_products(
    q: str | None = Query(None, max_length=100),
    category: str | None = Query(None, max_length=120),
    sort: str = Query("sku"),
    order: Literal["asc", "desc"] = "asc",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require(Perm.read)),
    db: Session = Depends(get_db),
) -> Page[ProductOut]:
    items, total = product_service.list_products(
        db, p.org_id, q=q, category=category, sort=sort, order=order, limit=limit, offset=offset
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/products/categories", response_model=list[str])
def categories(p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)) -> list[str]:
    from app.db.models import Product

    rows = db.scalars(
        select(Product.category).where(Product.org_id == p.org_id, Product.category.isnot(None)).distinct()
    ).all()
    return sorted(r for r in rows if r)


@router.get("/products/{product_id}", response_model=ProductOut)
def get_product(
    product_id: uuid.UUID, p: Principal = Depends(require(Perm.read)), db: Session = Depends(get_db)
) -> ProductOut:
    return product_service.product_out(db, p.org_id, product_id)


@router.get("/products/{product_id}/history", response_model=HistoryOut)
def history(
    product_id: uuid.UUID,
    days: int = Query(365, ge=7, le=3650),
    p: Principal = Depends(require(Perm.read)),
    db: Session = Depends(get_db),
) -> HistoryOut:
    prod = product_service.get_product(db, p.org_id, product_id)
    values, imputed = series.load_series(db, p.org_id, product_id)
    values, imputed = values.iloc[-days:], imputed.iloc[-days:]
    pts = [
        HistoryPoint(date=ts.date(), units=round(float(v), 4), imputed=bool(m))
        for ts, v, m in zip(values.index, values.values, imputed.values, strict=True)
    ]
    return HistoryOut(product_id=product_id, missing_policy=prod.missing_policy, points=pts)


@router.patch("/products/{product_id}/inventory", response_model=ProductOut)
def update_inventory(
    product_id: uuid.UUID,
    body: InventoryUpdate,
    p: Principal = Depends(require(Perm.manage_inventory)),
    db: Session = Depends(get_db),
) -> ProductOut:
    changes = body.model_dump(exclude_unset=True)
    product_service.update_inventory(db, p.org_id, product_id, dict(changes))
    audit.record_for(
        db,
        p,
        "inventory.update",
        resource_type="product",
        resource_id=product_id,
        details=body.model_dump(mode="json", exclude_unset=True),
    )
    db.commit()
    return product_service.product_out(db, p.org_id, product_id)


@router.post("/inventory/upload")
async def upload_inventory(
    file: UploadFile = File(...),
    p: Principal = Depends(require(Perm.manage_inventory)),
    db: Session = Depends(get_db),
) -> dict:
    data = await _read_limited(file, p)
    result = product_service.import_inventory_snapshot(db, p.org_id, data)
    audit.record_for(
        db,
        p,
        "inventory.snapshot_upload",
        resource_type="inventory",
        details={"updated": result["updated"], "unknown": result["unknown_count"]},
    )
    db.commit()
    return result
