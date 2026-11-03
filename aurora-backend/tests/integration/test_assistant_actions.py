"""Assistant proposals: the assistant proposes, the person confirms (ADR-046)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import func, select

from app.api.deps import Principal
from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.db.models import Alert, AssistantProposal, AuditEvent, ForecastOverride, Product, PurchaseOrder, User
from app.services import api_keys, assistant_actions, assistant_service, override_service
from tests.integration.test_assistant import _upload, fake_client, text, tool_use, with_key  # noqa: F401


def _principal(org, role, *, as_role=None, api_key=None):
    return Principal(
        user_id=org.users[role],
        org_id=org.org_id,
        role=as_role or role,
        email="x@example.com",
        ip=None,
        api_key=api_key,
    )


def _orders(db, org):
    return db.scalar(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.org_id == org.org_id))


def _alerts(db, org, n=2):
    rows = [
        Alert(org_id=org.org_id, kind="stockout_risk", severity="high", title=f"Alert {i}", dedup_key=f"t:{i}")
        for i in range(n)
    ]
    db.add_all(rows)
    db.commit()
    return rows


def test_claude_proposes_and_the_user_confirms(client, org, other_org, with_key, monkeypatch, db):  # noqa: F811
    prods = _upload(client, org)
    sku = prods[0]["sku"]
    fc, msgs = fake_client(
        NS(stop_reason="tool_use", content=[tool_use("propose_draft_purchase_orders", {"skus": ["NOPE"]})]),
        NS(
            stop_reason="tool_use",
            content=[tool_use("propose_draft_purchase_orders", {"skus": [sku], "quantities": {sku: 40}}, "tu_2")],
        ),
        NS(stop_reason="end_turn", content=[text("I've prepared a draft order for you to confirm.")]),
    )
    monkeypatch.setattr(assistant_service, "_client", lambda timeout_s: fc)
    r = client.post("/chat", json={"message": f"order 40 of {sku}"}, headers=org.h("operator"))
    assert r.status_code == 200, r.text
    body = r.json()
    names = {t["name"] for t in msgs.calls[0]["tools"]}
    assert {"propose_draft_purchase_orders", "propose_acknowledge_alerts"} <= names
    assert "propose_forecast_override" not in names  # operators cannot add overrides themselves
    bad = msgs.calls[1]["messages"][-1]["content"][0]
    assert bad["is_error"] is True and "Unknown SKU(s): NOPE" in bad["content"]
    [prop] = body["proposals"]
    assert prop["status"] == "pending" and f"{sku} (40)" in prop["summary"] and "Nothing is approved" in prop["summary"]
    assert _orders(db, org) == 0  # nothing changes until confirmed

    url = f"/assistant/proposals/{prop['id']}"
    assert [p["id"] for p in client.get("/assistant/proposals", headers=org.h("operator")).json()] == [prop["id"]]
    assert client.get("/assistant/proposals", headers=org.h("admin")).json() == []  # only the person asked
    assert client.post(f"{url}/apply", headers=org.h("admin")).status_code == 404
    assert client.post(f"{url}/apply", headers=other_org.h("admin")).status_code == 404
    done = client.post(f"{url}/apply", headers=org.h("operator"))
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "applied" and done.json()["result"]["orders"][0]["number"].startswith("PO-")
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.org_id == org.org_id))
    assert po.status == "draft"
    again = client.post(f"{url}/apply", headers=org.h("operator"))
    assert again.status_code == 409 and _orders(db, org) == 1
    actions = [
        a
        for (a,) in db.execute(
            select(AuditEvent.action).where(AuditEvent.org_id == org.org_id).order_by(AuditEvent.id)
        ).all()
    ]
    assert actions[-3:] == ["assistant.chat", "purchase_order.suggested", "assistant.action_applied"]
    assert "assistant.action_proposed" in actions
    ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "purchase_order.suggested"))
    assert ev.details["via"] == "assistant"


def test_fallback_proposes_acknowledging_alerts(client, org, db):
    rows = _alerts(db, org, 3)
    rows[2].status = "resolved"
    db.commit()
    r = client.post("/chat", json={"message": "Please acknowledge the alerts"}, headers=org.h("operator"))
    body = r.json()
    assert body["mode"] == "fallback"
    [prop] = body["proposals"]
    assert prop["summary"].startswith("Acknowledge 2 open alert(s)") and "confirm below" in body["answer"]
    # A viewer asking the same gets only the summary.
    assert (
        client.post("/chat", json={"message": "acknowledge alerts"}, headers=org.h("viewer")).json()["proposals"] == []
    )
    rows[1].status = "resolved"  # resolved in the meantime: skipped, not an error
    db.commit()
    res = client.post(f"/assistant/proposals/{prop['id']}/apply", headers=org.h("operator")).json()
    assert res["status"] == "applied" and res["result"] == {"acknowledged": 1, "skipped": 1}
    db.expire_all()
    assert db.get(Alert, rows[0].id).acknowledged_by == org.users["operator"]


def test_override_proposal_failure_and_role_checks(client, org, db):
    a = Product(org_id=org.org_id, sku="OV-1", name="Override me")
    db.add(a)
    db.commit()
    analyst = _principal(org, "analyst")
    args = {
        "sku": "OV-1", "start_date": "2099-01-01", "end_date": "2099-01-07", "kind": "scale", "value": 1.5,
        "reason": "Trade show",
    }  # fmt: skip
    with pytest.raises(Forbidden):
        assistant_actions.propose(db, _principal(org, "operator"), "add_forecast_override", args)
    with pytest.raises(ValidationFailed, match="reason"):
        assistant_actions.propose(db, analyst, "add_forecast_override", {**args, "reason": " "})
    with pytest.raises(ValidationFailed, match="at most"):
        assistant_actions.propose(db, analyst, "add_forecast_override", {**args, "end_date": "2100-06-01"})
    with pytest.raises(Forbidden, match="API keys"):
        assistant_actions.propose(db, _principal(org, "analyst", api_key="aur_x"), "add_forecast_override", args)

    first = assistant_actions.propose(db, analyst, "add_forecast_override", args)
    second = assistant_actions.propose(db, analyst, "add_forecast_override", args)
    db.commit()
    assert "OV-1 demand to the forecast × 1.5 from 2099-01-01 to 2099-01-07" in first.summary
    # The role was lowered since the proposal was made: refused at confirmation.
    with pytest.raises(Forbidden):
        assistant_actions.apply(db, _principal(org, "analyst", as_role="viewer"), first.id)
    db.rollback()
    assert assistant_actions.apply(db, analyst, first.id).status == "applied"
    db.commit()
    # The second now overlaps the first: it fails cleanly and creates nothing.
    failed = assistant_actions.apply(db, analyst, second.id)
    db.commit()
    assert failed.status == "failed" and "already has an override" in failed.result["error"]
    count = db.scalar(select(func.count()).select_from(ForecastOverride).where(ForecastOverride.org_id == org.org_id))
    assert count == 1
    assert override_service.active_for(db, org.org_id, a.id, date(2099, 1, 1), date(2099, 1, 7))[0].value == 1.5


def test_expiry_dismissal_limits_and_api_keys(client, org, db):
    _alerts(db, org, 1)
    op = _principal(org, "operator")
    ids = [str(x) for x in db.scalars(select(Alert.id).where(Alert.org_id == org.org_id))]
    old = assistant_actions.propose(db, op, "acknowledge_alerts", {"alert_ids": ids})
    keep = assistant_actions.propose(db, op, "acknowledge_alerts", {"alert_ids": ids})
    old.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    h = org.h("operator")
    assert [p["id"] for p in client.get("/assistant/proposals", headers=h).json()] == [str(keep.id)]
    r = client.post(f"/assistant/proposals/{old.id}/apply", headers=h)
    assert r.status_code == 409 and "expired" in r.json()["error"]["message"]
    assert client.post(f"/assistant/proposals/{keep.id}/dismiss", headers=h).json()["status"] == "dismissed"
    assert client.post(f"/assistant/proposals/{keep.id}/apply", headers=h).status_code == 409

    for _ in range(assistant_actions.MAX_PENDING):
        assistant_actions.propose(db, op, "acknowledge_alerts", {"alert_ids": ids})
    with pytest.raises(Conflict, match="unconfirmed proposals"):
        assistant_actions.propose(db, op, "acknowledge_alerts", {"alert_ids": ids})
    db.rollback()

    creator = db.get(User, org.users["operator"])
    _, token = api_keys.create(db, creator, name="robot", role="operator", expires_in_days=None)
    db.commit()
    kh = {"Authorization": f"Bearer {token}"}
    assert client.get("/assistant/proposals", headers=kh).status_code == 403
    assert client.post(f"/assistant/proposals/{keep.id}/apply", headers=kh).status_code == 403
    tools = assistant_service.tools_for(_principal(org, "operator", api_key="aur_x"))
    assert not [t for t in tools if t["name"].startswith("propose_")]

    old.created_at = datetime.now(UTC) - timedelta(days=31)
    db.commit()
    assert assistant_actions.purge_old(db, datetime.now(UTC)) == 1
    db.commit()
    assert db.get(AssistantProposal, old.id) is None
    assert json.dumps(assistant_actions.out(keep))  # serialisable for the API
