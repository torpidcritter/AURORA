"""Getting-started checklist: what a new organisation has set up so far, from its own data (ADR-041)."""

from __future__ import annotations

import uuid

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.db.models import DigestSchedule, ModelVersion, Product, SalesRecord, Supplier, User


def _any(db: Session, stmt) -> bool:  # type: ignore[no-untyped-def]
    return bool(db.scalar(select(exists(stmt))))


def checklist(db: Session, org_id: uuid.UUID, user: User) -> dict:
    users = db.scalar(select(func.count()).select_from(User).where(User.org_id == org_id, User.is_active)) or 0
    steps = [
        {
            "key": "sales",
            "title": "Upload sales history",
            "detail": "A CSV, Excel or JSON file of daily sales per product, or connect Shopify.",
            "href": "/upload",
            "done": _any(db, select(SalesRecord.id).where(SalesRecord.org_id == org_id)),
        },
        {
            "key": "stock",
            "title": "Enter stock on hand",
            "detail": "Recommendations need current stock; upload counts or edit them per product.",
            "href": "/products",
            "done": _any(db, select(Product.id).where(Product.org_id == org_id, Product.inventory_known)),
        },
        {
            "key": "supplier",
            "title": "Add a supplier",
            "detail": "Lead times and minimum orders make reorder suggestions realistic.",
            "href": "/suppliers",
            "done": _any(db, select(Supplier.id).where(Supplier.org_id == org_id)),
        },
        {
            "key": "model",
            "title": "Train the forecasting model",
            "detail": "Statistical methods work straight away; the Transformer needs a training run.",
            "href": "/models",
            "done": _any(db, select(ModelVersion.id).where(ModelVersion.org_id == org_id)),
        },
        {
            "key": "team",
            "title": "Invite a teammate",
            "detail": "Give planners and buyers their own accounts and roles.",
            "href": "/admin/users",
            "done": users > 1,
        },
        {
            "key": "digest",
            "title": "Schedule a digest",
            "detail": "A daily or weekly summary in the app, Slack or e-mail.",
            "href": "/digests",
            "done": _any(db, select(DigestSchedule.id).where(DigestSchedule.org_id == org_id)),
        },
        {
            "key": "two_factor",
            "title": "Turn on two-factor login",
            "detail": "Protect your own account with an authenticator app.",
            "href": "/account",
            "done": user.two_factor_enabled,
        },
    ]
    done = sum(1 for s in steps if s["done"])
    return {"steps": steps, "done": done, "total": len(steps), "complete": done == len(steps)}
