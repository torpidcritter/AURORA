"""Changes the assistant may propose, and their confirmation by a person (ADR-046).

The assistant never changes data. A `propose_*` tool validates its input, stores an `AssistantProposal` for the user
in the conversation and returns its summary; the user then confirms or dismisses it in the app. Confirming re-checks
the user's permission and runs the same service code as the matching screen, with the same audit events. Proposals
expire after a day and can be confirmed once, only by the user they were made for.

Proposals are built only from values the server resolved (SKUs and alert ids looked up in the user's organisation),
and the summary the user confirms is written by the server, not by the model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.security import Perm
from app.db.models import Alert, AssistantProposal, Product
from app.services import alert_service, audit, override_service, purchasing_service

TTL = timedelta(hours=24)
KEEP = timedelta(days=30)
MAX_PENDING = 20
MAX_PRODUCTS = 50
MAX_ALERTS = 25
MAX_QTY = 1_000_000

PERMS: dict[str, Perm] = {
    "draft_purchase_orders": Perm.manage_purchasing,
    "acknowledge_alerts": Perm.acknowledge_alerts,
    "add_forecast_override": Perm.manage_overrides,
}


def _now() -> datetime:
    return datetime.now(UTC)


def _skus(db: Session, org_id: uuid.UUID, skus: list[str]) -> dict[str, Product]:
    wanted = list(dict.fromkeys(str(s).strip() for s in skus if str(s).strip()))
    if not wanted:
        raise ValidationFailed("Name at least one product by SKU")
    found = {p.sku: p for p in db.scalars(select(Product).where(Product.org_id == org_id, Product.sku.in_(wanted)))}
    missing = [s for s in wanted if s not in found]
    if missing:
        raise ValidationFailed(f"Unknown SKU(s): {', '.join(missing[:10])}")
    return {s: found[s] for s in wanted}


def _date(v: Any, name: str) -> date:
    try:
        return date.fromisoformat(str(v))
    except ValueError as exc:
        raise ValidationFailed(f"{name} must be a date like 2026-12-01") from exc


# ---- building proposals -------------------------------------------------------------------------------------------


def _build_drafts(db: Session, p: Principal, args: dict) -> tuple[dict, str]:
    skus = args.get("skus") or []
    if not isinstance(skus, list) or len(skus) > MAX_PRODUCTS:
        raise ValidationFailed(f"Give between 1 and {MAX_PRODUCTS} SKUs")
    products = _skus(db, p.org_id, skus)
    raw_q = args.get("quantities") or {}
    if not isinstance(raw_q, dict):
        raise ValidationFailed("quantities must map SKU to units")
    quantities: dict[str, int] = {}
    for sku, q in raw_q.items():
        if sku not in products:
            raise ValidationFailed(f"Quantity given for {sku}, which is not in skus")
        try:
            n = int(q)
        except (TypeError, ValueError) as exc:
            raise ValidationFailed(f"Quantity for {sku} must be a whole number") from exc
        if not 1 <= n <= MAX_QTY:
            raise ValidationFailed(f"Quantity for {sku} must be between 1 and {MAX_QTY:,}")
        quantities[sku] = n
    if quantities and len(quantities) != len(products):
        raise ValidationFailed("Give a quantity for every SKU, or for none to use the suggested quantities")
    items = [{"product_id": str(pr.id), "sku": s, "quantity": quantities.get(s)} for s, pr in products.items()]
    shown = ", ".join(f"{i['sku']} ({i['quantity']})" if i["quantity"] else i["sku"] for i in items[:10])
    more = f" and {len(items) - 10} more" if len(items) > 10 else ""
    how = "these quantities" if quantities else "the suggested quantities"
    summary = f"Draft purchase orders (one per supplier) for {shown}{more}, using {how}. Nothing is approved or sent."
    return {"items": items}, summary


def _build_ack(db: Session, p: Principal, args: dict) -> tuple[dict, str]:
    ids = args.get("alert_ids") or []
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_ALERTS:
        raise ValidationFailed(f"Give between 1 and {MAX_ALERTS} alert ids")
    try:
        uids = list(dict.fromkeys(uuid.UUID(str(i)) for i in ids))
    except ValueError as exc:
        raise ValidationFailed("Alert ids must be the ids returned by get_alerts") from exc
    rows = db.scalars(select(Alert).where(Alert.org_id == p.org_id, Alert.id.in_(uids))).all()
    if len(rows) != len(uids):
        raise ValidationFailed("An alert was not found; use the ids returned by get_alerts")
    open_rows = [a for a in rows if a.status == "open"]
    if not open_rows:
        raise ValidationFailed("None of these alerts is open")
    titles = "; ".join(a.title[:80] for a in open_rows[:5])
    more = f" and {len(open_rows) - 5} more" if len(open_rows) > 5 else ""
    summary = f"Acknowledge {len(open_rows)} open alert(s): {titles}{more}."
    return {"alerts": [{"id": str(a.id), "title": a.title[:200]} for a in open_rows]}, summary


def _build_override(db: Session, p: Principal, args: dict) -> tuple[dict, str]:
    product = _skus(db, p.org_id, [args.get("sku") or ""])
    sku, pr = next(iter(product.items()))
    start, end = _date(args.get("start_date"), "start_date"), _date(args.get("end_date"), "end_date")
    kind = args.get("kind")
    if kind not in ("set", "scale"):
        raise ValidationFailed("kind must be 'set' (units per day) or 'scale' (multiply the forecast)")
    try:
        value = float(args.get("value"))  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValidationFailed("value must be a number") from exc
    reason = str(args.get("reason") or "").strip()[:500]
    if end < start:
        raise ValidationFailed("end_date must be on or after start_date")
    if (end - start).days + 1 > override_service.MAX_DAYS:
        raise ValidationFailed(f"An override can cover at most {override_service.MAX_DAYS} days")
    if kind == "scale" and not 0 <= value <= override_service.MAX_SCALE:
        raise ValidationFailed(f"A scale factor must be between 0 and {override_service.MAX_SCALE:g}")
    if kind == "set" and not 0 <= value <= override_service.MAX_UNITS:
        raise ValidationFailed("Units per day must be between 0 and 10,000,000")
    if not reason:
        raise ValidationFailed("Give a reason, so the override can be reviewed later")
    what = f"{value:g} units per day" if kind == "set" else f"the forecast × {value:g}"
    summary = f"Set {sku} demand to {what} from {start} to {end}. Reason: {reason}"
    payload = {
        "product_id": str(pr.id),
        "sku": sku,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "kind": kind,
        "value": value,
        "reason": reason,
    }
    return payload, summary


BUILDERS = {
    "draft_purchase_orders": _build_drafts,
    "acknowledge_alerts": _build_ack,
    "add_forecast_override": _build_override,
}


def propose(db: Session, p: Principal, kind: str, args: dict) -> AssistantProposal:
    if p.api_key:
        raise Forbidden("Proposals are for people signed in to Aurora, not API keys")
    if not p.can(PERMS[kind]):
        raise Forbidden("Your role cannot make this change")
    pending = db.scalar(
        select(func.count()).where(
            AssistantProposal.user_id == p.user_id,
            AssistantProposal.status == "pending",
            AssistantProposal.expires_at > _now(),
        )
    )
    if (pending or 0) >= MAX_PENDING:
        raise Conflict(f"You have {MAX_PENDING} unconfirmed proposals; confirm or dismiss some first")
    payload, summary = BUILDERS[kind](db, p, args if isinstance(args, dict) else {})
    row = AssistantProposal(
        org_id=p.org_id,
        user_id=p.user_id,
        kind=kind,
        payload=payload,
        summary=summary[:1000],
        expires_at=_now() + TTL,
    )
    db.add(row)
    db.flush()
    audit.record_for(
        db,
        p,
        "assistant.action_proposed",
        resource_type="assistant_proposal",
        resource_id=row.id,
        details={"kind": kind},
    )
    return row


def status_of(row: AssistantProposal, now: datetime | None = None) -> str:
    if row.status == "pending" and row.expires_at <= (now or _now()):
        return "expired"
    return row.status


def out(row: AssistantProposal) -> dict:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "summary": row.summary,
        "status": status_of(row),
        "result": row.result,
        "created_at": row.created_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
    }


def list_pending(db: Session, p: Principal) -> list[AssistantProposal]:
    return list(
        db.scalars(
            select(AssistantProposal)
            .where(
                AssistantProposal.org_id == p.org_id,
                AssistantProposal.user_id == p.user_id,
                AssistantProposal.status == "pending",
                AssistantProposal.expires_at > _now(),
            )
            .order_by(AssistantProposal.created_at.desc())
        )
    )


def _get_own(db: Session, p: Principal, proposal_id: uuid.UUID, *, lock: bool = False) -> AssistantProposal:
    stmt = select(AssistantProposal).where(
        AssistantProposal.id == proposal_id,
        AssistantProposal.org_id == p.org_id,
        AssistantProposal.user_id == p.user_id,  # only the person it was proposed to
    )
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise NotFound("Proposal not found")
    return row


def _decidable(row: AssistantProposal) -> None:
    st = status_of(row)
    if st != "pending":
        raise Conflict(f"This proposal is {st}", code="proposal_closed")


# ---- confirming -----------------------------------------------------------------------------------------------------


def _apply_drafts(db: Session, p: Principal, payload: dict) -> dict:
    items = payload["items"]
    ids = [uuid.UUID(i["product_id"]) for i in items]
    quantities = {uuid.UUID(i["product_id"]): int(i["quantity"]) for i in items if i.get("quantity")}
    res = purchasing_service.suggest(db, p.org_id, p.user_id, ids, quantities or None)
    numbers = []
    for po in res["created"]:
        n = purchasing_service.order_number(po.number)
        numbers.append({"id": str(po.id), "number": n})
        audit.record_for(
            db,
            p,
            "purchase_order.suggested",
            resource_type="purchase_order",
            resource_id=po.id,
            details={"number": n, "status": po.status, "via": "assistant"},
        )
    return {"orders": numbers, "skipped": res["skipped"]}


def _apply_ack(db: Session, p: Principal, payload: dict) -> dict:
    done, skipped = 0, 0
    for a in payload["alerts"]:
        aid = uuid.UUID(a["id"])
        try:
            alert_service.acknowledge(db, p.org_id, aid, p.user_id)
        except (Conflict, NotFound):
            skipped += 1  # resolved or deleted since it was proposed
            continue
        audit.record_for(
            db, p, "alert.acknowledge", resource_type="alert", resource_id=aid, details={"via": "assistant"}
        )
        done += 1
    return {"acknowledged": done, "skipped": skipped}


def _apply_override(db: Session, p: Principal, payload: dict) -> dict:
    o = override_service.create(
        db,
        p.org_id,
        p.user_id,
        product_id=uuid.UUID(payload["product_id"]),
        start_date=date.fromisoformat(payload["start_date"]),
        end_date=date.fromisoformat(payload["end_date"]),
        kind=payload["kind"],
        value=float(payload["value"]),
        reason=payload["reason"],
    )
    audit.record_for(
        db,
        p,
        "forecast_override.created",
        resource_type="product",
        resource_id=o.product_id,
        details={
            "override_id": str(o.id),
            "kind": o.kind,
            "value": o.value,
            "start_date": o.start_date.isoformat(),
            "end_date": o.end_date.isoformat(),
            "via": "assistant",
        },
    )
    return {"override_id": str(o.id), "product_id": payload["product_id"]}


APPLIERS = {
    "draft_purchase_orders": _apply_drafts,
    "acknowledge_alerts": _apply_ack,
    "add_forecast_override": _apply_override,
}


def apply(db: Session, p: Principal, proposal_id: uuid.UUID) -> AssistantProposal:
    """Run a proposal for its user. On a business error the change is rolled back and the proposal marked failed."""
    row = _get_own(db, p, proposal_id, lock=True)
    _decidable(row)
    if not p.can(PERMS[row.kind]):  # the role may have changed since it was proposed
        raise Forbidden("Your role cannot make this change")
    try:
        with db.begin_nested():
            result = APPLIERS[row.kind](db, p, row.payload)
    except (ValidationFailed, Conflict, NotFound) as exc:
        row.status, row.result, row.decided_at = "failed", {"error": exc.message}, _now()
        audit.record_for(
            db,
            p,
            "assistant.action_failed",
            resource_type="assistant_proposal",
            resource_id=row.id,
            details={"kind": row.kind, "error": exc.message[:300]},
        )
        db.flush()
        return row
    row.status, row.result, row.decided_at = "applied", result, _now()
    audit.record_for(
        db,
        p,
        "assistant.action_applied",
        resource_type="assistant_proposal",
        resource_id=row.id,
        details={"kind": row.kind},
    )
    db.flush()
    return row


def dismiss(db: Session, p: Principal, proposal_id: uuid.UUID) -> AssistantProposal:
    row = _get_own(db, p, proposal_id, lock=True)
    _decidable(row)
    row.status, row.decided_at = "dismissed", _now()
    audit.record_for(
        db,
        p,
        "assistant.action_dismissed",
        resource_type="assistant_proposal",
        resource_id=row.id,
        details={"kind": row.kind},
    )
    db.flush()
    return row


def purge_old(db: Session, now: datetime) -> int:
    res = db.execute(delete(AssistantProposal).where(AssistantProposal.created_at < now - KEEP))
    return int(res.rowcount or 0)  # type: ignore[attr-defined]
