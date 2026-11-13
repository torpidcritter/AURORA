"""Build regular daily demand series from stored sales records."""

from __future__ import annotations

import uuid
from datetime import date

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Product, SalesRecord


def fill_series(observed: pd.Series, policy: str, end: date | None = None) -> tuple[pd.Series, pd.Series]:
    """Reindex to a daily range. Returns (values, imputed_mask).

    policy "zero": absent dates are treated as zero sales (typical of POS exports that omit no-sale days).
    policy "interpolate": absent dates are unknown and linearly interpolated between observations.
    Explicit zeros in the data are always kept as confirmed zeros.
    """
    if observed.empty:
        return observed.astype(float), observed.astype(bool)
    observed = observed.sort_index()
    start = observed.index.min()
    stop = pd.Timestamp(end) if end is not None else observed.index.max()
    idx = pd.date_range(start, max(stop, observed.index.max()), freq="D")
    s = observed.reindex(idx)
    imputed = s.isna()
    if policy == "interpolate":
        s = s.interpolate(method="linear", limit_direction="both")
    s = s.fillna(0.0).clip(lower=0.0)
    return s.astype(float), imputed


def load_series(
    db: Session, org_id: uuid.UUID, product_id: uuid.UUID, end: date | None = None
) -> tuple[pd.Series, pd.Series]:
    prod = db.get(Product, product_id)
    if prod is None or prod.org_id != org_id:
        return pd.Series(dtype=float), pd.Series(dtype=bool)
    rows = db.execute(
        select(SalesRecord.sale_date, func.sum(SalesRecord.quantity))
        .where(SalesRecord.product_id == product_id, SalesRecord.org_id == org_id)
        .group_by(SalesRecord.sale_date)
        .order_by(SalesRecord.sale_date)
    ).all()
    if not rows:
        return pd.Series(dtype=float), pd.Series(dtype=bool)
    obs = pd.Series({pd.Timestamp(d): float(q) for d, q in rows})
    return fill_series(obs, prod.missing_policy, end)


def load_many(db: Session, org_id: uuid.UUID, product_ids: list[uuid.UUID] | None = None) -> dict[uuid.UUID, pd.Series]:
    """Load filled series for several products in one query (used by training and batch jobs)."""
    q = (
        select(SalesRecord.product_id, SalesRecord.sale_date, func.sum(SalesRecord.quantity))
        .where(SalesRecord.org_id == org_id)
        .group_by(SalesRecord.product_id, SalesRecord.sale_date)
    )
    if product_ids is not None:
        q = q.where(SalesRecord.product_id.in_(product_ids))
    df = pd.DataFrame(db.execute(q).all(), columns=["product_id", "sale_date", "qty"])
    if df.empty:
        return {}
    policies = dict(
        db.execute(select(Product.id, Product.missing_policy).where(Product.org_id == org_id)).tuples().all()
    )
    out: dict[uuid.UUID, pd.Series] = {}
    for pid, g in df.groupby("product_id"):
        obs = pd.Series(g["qty"].astype(float).values, index=pd.to_datetime(g["sale_date"]))
        out[pid] = fill_series(obs, policies.get(pid, "zero"))[0]  # type: ignore[index]
    return out
