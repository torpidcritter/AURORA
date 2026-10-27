"""Persist uploaded datasets: store the raw file, upsert products and sales in one transaction."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.db.models import Dataset, Product, SalesRecord
from app.services import ingestion
from app.storage import get_storage, sha256

CHUNK = 5000


@dataclass
class UploadOutcome:
    dataset: Dataset
    duplicate: bool


def ingest_upload(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    filename: str,
    data: bytes,
    tags: dict[str, str],
    missing_policy: str,
) -> UploadOutcome:
    s = get_settings()
    if not data:
        raise ValidationFailed("The uploaded file is empty")
    if len(data) > s.max_upload_bytes:
        raise ValidationFailed(f"File exceeds the {s.max_upload_bytes // (1024 * 1024)} MB limit")
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ")[:200] or "upload"
    digest = sha256(data)
    existing = db.scalar(select(Dataset).where(Dataset.org_id == org_id, Dataset.sha256 == digest))
    if existing is not None:
        return UploadOutcome(existing, duplicate=True)

    try:
        fmt = ingestion.detect_format(safe_name, data)
    except ingestion.IngestionError as exc:
        raise ValidationFailed(str(exc), details=exc.details) from exc

    dataset_id = uuid.uuid4()
    key = f"datasets/{org_id}/{dataset_id}.{fmt}"
    report: dict[str, object]
    status = "imported"
    mapping: dict[str, str] = {}
    result: ingestion.CleanResult | None = None
    try:
        frame = ingestion.read_frame(fmt, data, s.max_upload_rows)
        result = ingestion.clean(frame)
        report = result.report()
        mapping = result.mapping
        if result.accepted_rows == 0:
            status = "rejected"
            report["rejection_reason"] = "No valid rows"
    except ingestion.IngestionError as exc:
        status = "rejected"
        report = {"rejection_reason": str(exc), "details": exc.details, "quality_score": 0.0}

    get_storage().put(key, data)
    ds = Dataset(
        id=dataset_id,
        org_id=org_id,
        uploaded_by=user_id,
        filename=safe_name,
        file_format=fmt,
        storage_key=key,
        size_bytes=len(data),
        sha256=digest,
        status=status,
        tags=tags,
        missing_policy=missing_policy,
        row_count=result.total_rows if result else 0,
        accepted_rows=result.accepted_rows if result else 0,
        rejected_rows=result.rejected_rows if result else 0,
        quality_score=float(report.get("quality_score", 0.0) or 0.0),  # type: ignore[arg-type]
        quality_report=report,
        column_mapping=mapping,
    )
    db.add(ds)
    db.flush()
    if status == "imported" and result is not None:
        _import_rows(db, org_id, ds.id, result.frame, missing_policy)
    return UploadOutcome(ds, duplicate=False)


def _import_rows(
    db: Session, org_id: uuid.UUID, dataset_id: uuid.UUID, frame: pd.DataFrame, missing_policy: str
) -> None:
    meta = frame.groupby("sku").agg(name=("product_name", "last"), category=("category", "last")).reset_index()
    prod_rows = [
        {
            "id": uuid.uuid4(),
            "org_id": org_id,
            "sku": r.sku,
            "name": (r.name or r.sku)[:300],
            "category": (r.category or None),
            "missing_policy": missing_policy,
        }
        for r in meta.itertuples()
    ]
    for i in range(0, len(prod_rows), CHUNK):
        stmt = insert(Product).values(prod_rows[i : i + CHUNK])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_products_org_sku",
            set_={"missing_policy": stmt.excluded.missing_policy},
        )
        db.execute(stmt)
    ids = dict(db.execute(select(Product.sku, Product.id).where(Product.org_id == org_id)).tuples().all())
    sales = [
        {
            "org_id": org_id,
            "product_id": ids[r.sku],
            "sale_date": r.sale_date,
            "quantity": float(r.quantity),
            "source": "upload",
            "dataset_id": dataset_id,
        }
        for r in frame.itertuples()
    ]
    for i in range(0, len(sales), CHUNK):
        stmt = insert(SalesRecord).values(sales[i : i + CHUNK])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_sales_product_date_source",
            set_={"quantity": stmt.excluded.quantity, "dataset_id": stmt.excluded.dataset_id},
        )
        db.execute(stmt)
