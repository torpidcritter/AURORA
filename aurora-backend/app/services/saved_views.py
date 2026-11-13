"""Saved views: named filter sets for list pages (ADR-047).

Only known filter keys are stored for each page, with short string or true/false values, so a saved view cannot
carry anything but filters. A view is private unless its owner shares it with the organisation; only the owner or
an admin can delete it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.security import Perm
from app.db.models import SavedView

PAGES: dict[str, frozenset[str]] = {
    "products": frozenset({"q", "category", "health", "sort", "order"}),
    "inventory": frozenset({"level", "only_reorder"}),
    "purchase_orders": frozenset({"status"}),
    "alerts": frozenset({"status", "severity"}),
}
MAX_PER_PAGE = 30
MAX_VALUE = 200


def clean_params(page: str, params: dict) -> dict:
    if page not in PAGES:
        raise ValidationFailed(f"Views can be saved for: {', '.join(sorted(PAGES))}")
    out: dict[str, str | bool] = {}
    for k, v in params.items():
        if k not in PAGES[page]:
            raise ValidationFailed(f"'{k}' is not a filter on this page")
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, str) and len(v) <= MAX_VALUE:
            if v != "":
                out[k] = v
        else:
            raise ValidationFailed(f"'{k}' must be text of at most {MAX_VALUE} characters, or true/false")
    return out


def out(v: SavedView, p: Principal) -> dict:
    return {
        "id": str(v.id),
        "page": v.page,
        "name": v.name,
        "params": v.params,
        "shared": v.shared,
        "mine": v.user_id == p.user_id,
        "created_at": v.created_at.isoformat(),
    }


def list_for(db: Session, p: Principal, page: str) -> list[SavedView]:
    return list(
        db.scalars(
            select(SavedView)
            .where(
                SavedView.org_id == p.org_id,
                SavedView.page == page,
                or_(SavedView.user_id == p.user_id, SavedView.shared.is_(True)),
            )
            .order_by(SavedView.user_id != p.user_id, func.lower(SavedView.name))
        )
    )


def create(db: Session, p: Principal, *, page: str, name: str, params: dict, shared: bool) -> SavedView:
    name = name.strip()
    if not name:
        raise ValidationFailed("Give the view a name")
    cleaned = clean_params(page, params)
    count = db.scalar(select(func.count()).where(SavedView.user_id == p.user_id, SavedView.page == page))
    if (count or 0) >= MAX_PER_PAGE:
        raise Conflict(f"You can keep at most {MAX_PER_PAGE} views per page; delete one first")
    v = SavedView(org_id=p.org_id, user_id=p.user_id, page=page, name=name[:80], params=cleaned, shared=shared)
    db.add(v)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        raise Conflict("You already have a view with this name on this page", code="view_name_taken") from exc
    return v


def delete(db: Session, p: Principal, view_id: uuid.UUID) -> SavedView:
    v = db.scalar(select(SavedView).where(SavedView.id == view_id, SavedView.org_id == p.org_id))
    if v is None or (v.user_id != p.user_id and not v.shared):
        raise NotFound("View not found")
    if v.user_id != p.user_id and not p.can(Perm.manage_users):
        raise Forbidden("Only its owner or an admin can delete a shared view")
    db.delete(v)
    return v
