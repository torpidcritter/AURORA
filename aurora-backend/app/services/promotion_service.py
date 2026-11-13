"""Promotions: measure past ones, estimate and apply planned ones (ADR-045).

Measuring: for each product, the average daily sales during the promotion are compared with the 28 days before it
(days inside other promotions are left out of that baseline). The lift is during / before; the dip is the 14 days
after compared with before, which shows customers stocking up. Products with no sales before the promotion have no
baseline and are reported without a lift.

Estimating: a planned promotion's uplift is the median lift of the same products' past promotions; failing that,
of past promotions in the same category; failing that, of all past promotions. Past promotions with a discount
within 10 points of the planned one are preferred when there are any. The planner can always enter their own value.

Applying: on each promotion day the forecast used for decisions is multiplied by the uplift (see
override_service.adjust_points). A planner override on the same day wins, because it is the more specific decision.
"""

from __future__ import annotations

import statistics
import uuid
from datetime import date, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import Product, Promotion
from app.services import series
from app.services.product_service import org_as_of

BASELINE_DAYS = 28
DIP_DAYS = 14
MAX_DAYS = 92
MAX_PRODUCTS = 500
MAX_UPLIFT = 10.0
SIMILAR_DISCOUNT = 10.0


def _today(db: Session, org_id: uuid.UUID) -> date:
    return org_as_of(db, org_id) or date.today()


def status(p: Promotion, today: date) -> str:
    if p.start_date > today:
        return "planned"
    return "running" if p.end_date >= today else "finished"


def get(db: Session, org_id: uuid.UUID, promo_id: uuid.UUID, *, lock: bool = False) -> Promotion:
    stmt = select(Promotion).where(Promotion.id == promo_id, Promotion.org_id == org_id)
    p = db.scalar(stmt.with_for_update() if lock else stmt)
    if p is None:
        raise NotFound("Promotion not found")
    return p


def _resolve_products(db: Session, org_id: uuid.UUID, product_ids: list[uuid.UUID], category: str | None) -> list[str]:
    if category:
        ids = list(db.scalars(select(Product.id).where(Product.org_id == org_id, Product.category == category)))
        if not ids:
            raise ValidationFailed(f"No products in the category {category}")
    else:
        ids = list(dict.fromkeys(product_ids))
        if not ids:
            raise ValidationFailed("Choose the products or a category on promotion")
        found = set(db.scalars(select(Product.id).where(Product.org_id == org_id, Product.id.in_(ids))))
        if found != set(ids):
            raise ValidationFailed("A product was not found")
    if len(ids) > MAX_PRODUCTS:
        raise ValidationFailed(f"A promotion can cover at most {MAX_PRODUCTS} products")
    return [str(i) for i in ids]


def _check(start: date, end: date, uplift: float | None, discount: float | None) -> None:
    if end < start:
        raise ValidationFailed("The end date must be on or after the start date")
    if (end - start).days + 1 > MAX_DAYS:
        raise ValidationFailed(f"A promotion can last at most {MAX_DAYS} days")
    if uplift is not None and not 0 < uplift <= MAX_UPLIFT:
        raise ValidationFailed(f"Uplift must be above 0 and at most {MAX_UPLIFT:g} (×)")
    if discount is not None and not 0 <= discount <= 95:
        raise ValidationFailed("Discount must be between 0 and 95%")


# ---- measuring ----------------------------------------------------------------------------------------------------


def measure(db: Session, org_id: uuid.UUID, p: Promotion) -> dict:
    """Lift and dip of a promotion that has started, per product and overall (weighted by baseline units)."""
    ids = [uuid.UUID(x) for x in p.product_ids]
    loaded = series.load_many(db, org_id, ids)
    others = db.scalars(
        select(Promotion).where(
            Promotion.org_id == org_id,
            Promotion.id != p.id,
            Promotion.start_date <= p.end_date + timedelta(days=DIP_DAYS),
            Promotion.end_date >= p.start_date - timedelta(days=BASELINE_DAYS),
        )
    ).all()
    skus = dict(db.execute(select(Product.id, Product.sku).where(Product.id.in_(ids))).tuples().all())
    rows, base_total, during_total, n_during_days = [], 0.0, 0.0, 0
    for pid in ids:
        y = loaded.get(pid)
        row: dict = {"product_id": str(pid), "sku": skus.get(pid), "lift": None, "dip": None}
        if y is None or y.empty:
            rows.append({**row, "note": "no sales data"})
            continue
        s = y.copy()
        s.index = s.index.date  # type: ignore[assignment]

        def window(a: date, b: date, *, skip_other: bool, s=s, pid=pid) -> list[float]:
            vals = []
            d = a
            while d <= b:
                if d in s.index and not (
                    skip_other and any(o.start_date <= d <= o.end_date and str(pid) in o.product_ids for o in others)
                ):
                    vals.append(float(s[d]))
                d += timedelta(days=1)
            return vals

        before = window(p.start_date - timedelta(days=BASELINE_DAYS), p.start_date - timedelta(days=1), skip_other=True)
        during = window(p.start_date, p.end_date, skip_other=False)
        after = window(p.end_date + timedelta(days=1), p.end_date + timedelta(days=DIP_DAYS), skip_other=True)
        if not during:
            rows.append({**row, "note": "no sales data during the promotion yet"})
            continue
        if len(before) < 7 or np.mean(before) <= 0:
            rows.append({**row, "during_per_day": round(float(np.mean(during)), 3), "note": "no baseline before it"})
            continue
        b, du = float(np.mean(before)), float(np.mean(during))
        row.update(
            {
                "baseline_per_day": round(b, 3),
                "during_per_day": round(du, 3),
                "lift": round(du / b, 3),
                "incremental_units": round((du - b) * len(during), 1),
            }
        )
        if len(after) >= 7:
            row["dip"] = round(float(np.mean(after)) / b, 3)
        rows.append(row)
        base_total += b * len(during)
        during_total += du * len(during)
        n_during_days = max(n_during_days, len(during))
    measured = [r for r in rows if r["lift"] is not None]
    return {
        "products": rows,
        "measured_products": len(measured),
        "lift": round(during_total / base_total, 3) if base_total > 0 else None,
        "incremental_units": round(during_total - base_total, 1) if base_total > 0 else None,
        "days_observed": n_during_days,
    }


# ---- estimating ---------------------------------------------------------------------------------------------------


def estimate(
    db: Session,
    org_id: uuid.UUID,
    product_ids: list[str],
    discount: float | None,
    *,
    exclude: uuid.UUID | None = None,
) -> dict:
    """Suggested uplift for a planned promotion from comparable past ones."""
    today = _today(db, org_id)
    past = [
        p
        for p in db.scalars(select(Promotion).where(Promotion.org_id == org_id, Promotion.end_date < today)).all()
        if p.id != exclude
    ]
    if not past:
        return {"uplift": None, "basis": "no past promotions to learn from", "promotions": 0}
    wanted = set(product_ids)
    cats = set(
        db.scalars(
            select(Product.category).where(
                Product.id.in_([uuid.UUID(x) for x in wanted]), Product.category.is_not(None)
            )
        )
    )
    cat_of = dict(db.execute(select(Product.id, Product.category).where(Product.org_id == org_id)).tuples().all())

    def lifts(promos: list[Promotion], keep) -> list[tuple[float, Promotion]]:  # type: ignore[no-untyped-def]
        out = []
        for p in promos:
            m = measure(db, org_id, p)
            vals = [r["lift"] for r in m["products"] if r["lift"] is not None and keep(r["product_id"])]
            out.extend((v, p) for v in vals)
        return out

    tiers = [
        ("the same products' past promotions", lambda pid: pid in wanted),
        ("past promotions in the same category", lambda pid: cat_of.get(uuid.UUID(pid)) in cats),
        ("all past promotions", lambda pid: True),
    ]
    for label, keep in tiers:
        found = lifts(past, keep)
        if not found:
            continue
        if discount is not None:
            near = [
                (v, p)
                for v, p in found
                if p.discount_pct is not None and abs(p.discount_pct - discount) <= SIMILAR_DISCOUNT
            ]
            if near:
                found, label = near, f"{label} with a similar discount"
        value = round(min(max(statistics.median(v for v, _ in found), 0.1), MAX_UPLIFT), 2)
        return {
            "uplift": value,
            "basis": label,
            "promotions": len({p.id for _, p in found}),
            "observations": len(found),
        }
    return {"uplift": None, "basis": "past promotions had no measurable lift", "promotions": len(past)}


# ---- applying -----------------------------------------------------------------------------------------------------


def active_for(db: Session, org_id: uuid.UUID, product_id: uuid.UUID, start: date, end: date) -> list[Promotion]:
    """Promotions with an uplift that cover the product on any day in [start, end]."""
    rows = db.scalars(
        select(Promotion).where(
            Promotion.org_id == org_id,
            Promotion.uplift.is_not(None),
            Promotion.start_date <= end,
            Promotion.end_date >= start,
            Promotion.product_ids.contains([str(product_id)]),
        )
    ).all()
    return sorted(rows, key=lambda p: p.start_date)


# ---- CRUD ---------------------------------------------------------------------------------------------------------


def out(db: Session, org_id: uuid.UUID, p: Promotion, *, detail: bool = False) -> dict:
    today = _today(db, org_id)
    st = status(p, today)
    body = {
        "id": str(p.id),
        "name": p.name,
        "start_date": p.start_date.isoformat(),
        "end_date": p.end_date.isoformat(),
        "product_ids": list(p.product_ids),
        "product_count": len(p.product_ids),
        "category": p.category,
        "discount_pct": p.discount_pct,
        "uplift": p.uplift,
        "uplift_source": p.uplift_source,
        "notes": p.notes,
        "status": st,
        "version": p.version,
        "created_at": p.created_at.isoformat(),
    }
    if detail:
        body["measurement"] = measure(db, org_id, p) if st != "planned" else None
        body["estimate"] = (
            estimate(db, org_id, list(p.product_ids), p.discount_pct, exclude=p.id) if st != "finished" else None
        )
    return body


def create(db: Session, org_id: uuid.UUID, user_id: uuid.UUID, v: dict) -> Promotion:
    _check(v["start_date"], v["end_date"], v.get("uplift"), v.get("discount_pct"))
    if not v["name"].strip():
        raise ValidationFailed("Give the promotion a name")
    products = _resolve_products(db, org_id, v.get("product_ids") or [], v.get("category"))
    uplift, source = v.get("uplift"), "planner"
    if uplift is None and v.get("use_estimate", True):
        est = estimate(db, org_id, products, v.get("discount_pct"))
        uplift, source = est["uplift"], "estimated" if est["uplift"] is not None else "none"
    p = Promotion(
        org_id=org_id,
        name=v["name"].strip()[:200],
        start_date=v["start_date"],
        end_date=v["end_date"],
        product_ids=products,
        category=v.get("category") or None,
        discount_pct=v.get("discount_pct"),
        uplift=uplift,
        uplift_source=source if uplift is not None else "none",
        notes=(v.get("notes") or "").strip()[:500],
        created_by=user_id,
    )
    db.add(p)
    db.flush()
    return p


def update(db: Session, org_id: uuid.UUID, promo_id: uuid.UUID, changes: dict) -> Promotion:
    p = get(db, org_id, promo_id, lock=True)
    expected = changes.pop("expected_version", None)
    if expected is not None and expected != p.version:
        raise Conflict("Someone else changed this promotion; reload the page")
    if status(p, _today(db, org_id)) == "finished" and set(changes) - {"notes", "name"}:
        raise Conflict("A finished promotion is a record of what happened; only its name and notes can change")
    start, end = changes.get("start_date", p.start_date), changes.get("end_date", p.end_date)
    _check(start, end, changes.get("uplift"), changes.get("discount_pct", p.discount_pct))
    p.start_date, p.end_date = start, end
    if "product_ids" in changes or "category" in changes:
        p.product_ids = _resolve_products(db, org_id, changes.get("product_ids") or [], changes.get("category"))
        p.category = changes.get("category") or None
    if "discount_pct" in changes:
        p.discount_pct = changes["discount_pct"]
    if "uplift" in changes:
        p.uplift = changes["uplift"]
        p.uplift_source = "planner" if p.uplift is not None else "none"
    if "name" in changes:
        if not changes["name"].strip():
            raise ValidationFailed("Give the promotion a name")
        p.name = changes["name"].strip()[:200]
    if "notes" in changes:
        p.notes = (changes["notes"] or "").strip()[:500]
    p.version += 1
    db.flush()
    return p


def delete(db: Session, org_id: uuid.UUID, promo_id: uuid.UUID) -> Promotion:
    p = get(db, org_id, promo_id, lock=True)
    db.delete(p)
    return p


def list_for_org(db: Session, org_id: uuid.UUID) -> list[Promotion]:
    return list(db.scalars(select(Promotion).where(Promotion.org_id == org_id).order_by(Promotion.start_date.desc())))
