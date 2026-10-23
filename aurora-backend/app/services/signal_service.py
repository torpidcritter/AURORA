from __future__ import annotations

import io
import uuid
from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.db.models import ExternalSignal, Product

SIGNAL_TYPES = ("holiday", "promotion", "weather", "festival", "event", "other")


def create(
    db: Session,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    *,
    name: str,
    signal_type: str,
    start_date: date,
    end_date: date,
    magnitude: float,
    sku: str | None = None,
) -> ExternalSignal:
    if signal_type not in SIGNAL_TYPES:
        raise ValidationFailed(f"signal_type must be one of {SIGNAL_TYPES}")
    if not -1 <= magnitude <= 1:
        raise ValidationFailed("magnitude must be between -1.0 and 1.0")
    if end_date < start_date:
        raise ValidationFailed("end_date must be on or after start_date")
    if (end_date - start_date).days > 366:
        raise ValidationFailed("signals may span at most one year")
    product_id = None
    if sku:
        product_id = db.scalar(select(Product.id).where(Product.org_id == org_id, Product.sku == sku))
        if product_id is None:
            raise ValidationFailed(f"Unknown SKU {sku!r}")
    s = ExternalSignal(
        org_id=org_id,
        product_id=product_id,
        name=name[:200],
        signal_type=signal_type,
        start_date=start_date,
        end_date=end_date,
        magnitude=magnitude,
        created_by=user_id,
    )
    db.add(s)
    db.flush()
    return s


def import_csv(db: Session, org_id: uuid.UUID, user_id: uuid.UUID | None, data: bytes) -> dict:
    try:
        df = pd.read_csv(io.StringIO(data.decode("utf-8-sig")), dtype=str, keep_default_na=False, nrows=5001)
    except Exception as exc:  # noqa: BLE001
        raise ValidationFailed(f"Could not parse signals CSV: {type(exc).__name__}") from exc
    need = {"name", "signal_type", "start_date", "end_date", "magnitude"}
    cols = {c.strip().lower(): c for c in df.columns}
    if not need <= set(cols):
        raise ValidationFailed(f"Signals CSV needs columns {sorted(need)} (optional: sku)")
    if len(df) > 5000:
        raise ValidationFailed("At most 5000 signals per upload")
    created, errors = 0, []
    for i, r in df.iterrows():
        try:
            with db.begin_nested():
                create(
                    db,
                    org_id,
                    user_id,
                    name=str(r[cols["name"]]).strip(),
                    signal_type=str(r[cols["signal_type"]]).strip().lower(),
                    start_date=date.fromisoformat(str(r[cols["start_date"]]).strip()),
                    end_date=date.fromisoformat(str(r[cols["end_date"]]).strip()),
                    magnitude=float(r[cols["magnitude"]]),
                    sku=(str(r[cols["sku"]]).strip() or None) if "sku" in cols else None,
                )
            created += 1
        except (ValidationFailed, ValueError) as exc:
            errors.append({"row": int(i) + 2, "message": getattr(exc, "message", str(exc))[:200]})  # type: ignore[call-overload]
    return {"created": created, "errors": errors[:100], "error_count": len(errors)}


def delete(db: Session, org_id: uuid.UUID, signal_id: uuid.UUID) -> None:
    s = db.get(ExternalSignal, signal_id)
    if s is None or s.org_id != org_id:
        raise NotFound("Signal not found")
    db.delete(s)
