"""Assistant: permission-scoped read-only tools, Claude tool loop (with a fake client) and deterministic fallback."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace as NS

import anthropic
import httpx
import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.db.models import Product
from app.devtools.synthetic import generate_sales, to_csv_bytes
from app.services import assistant_service


def _upload(client, org, n=3):
    df = generate_sales(n_products=n, days=120)
    r = client.post(
        "/datasets/upload",
        files={"file": ("s.csv", io.BytesIO(to_csv_bytes(df)), "text/csv")},
        headers=org.h("operator"),
    )
    assert r.status_code == 201
    prods = client.get("/products", headers=org.h()).json()["items"]
    for p in prods:
        client.patch(
            f"/products/{p['id']}/inventory",
            json={"expected_version": p["version"], "on_hand": 5, "lead_time_days": 7},
            headers=org.h("operator"),
        )
    return prods


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=lambda o: getattr(o, "__dict__", str(o)))))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def fake_client(*script):
    msgs = FakeMessages(script)
    return NS(beta=NS(messages=msgs)), msgs


def text(t):
    return NS(type="text", text=t)


def tool_use(name, args=None, id_="tu_1"):
    return NS(type="tool_use", id=id_, name=name, input=args or {})


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", SecretStr("test-key-not-real"))


def test_fallback_without_api_key_uses_real_data(client, org):
    _upload(client, org)
    r = client.post("/chat", json={"message": "Which products are at risk?"}, headers=org.h("viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "fallback"
    assert "not configured" in body["notice"]
    assert "Stockout risk:" in body["answer"]
    assert body["tool_calls"] == [{"tool": "get_risk_summary", "ok": True}]
    ev = client.get("/audit?action=assistant.chat", headers=org.h("admin")).json()["items"][0]
    assert ev["details"] == {"mode": "fallback", "tools": ["get_risk_summary"]}
    assert "Which products" not in json.dumps(ev)  # the question itself is not stored


def test_claude_tool_loop_scoped_to_caller_org(client, org, other_org, with_key, monkeypatch, db):
    _upload(client, org)
    _upload(client, other_org)
    # Rename one of the other org's products so leakage would be detectable.
    other = db.query(Product).filter(Product.org_id == other_org.org_id).first()
    other.name = "OTHER-ORG-SECRET"
    db.commit()
    fc, msgs = fake_client(
        NS(stop_reason="tool_use", content=[text("Checking."), tool_use("list_products", {"limit": 50})]),
        NS(stop_reason="end_turn", content=[text("You have 3 products.")]),
    )
    monkeypatch.setattr(assistant_service, "_client", lambda timeout_s: fc)
    r = client.post("/chat", json={"message": "list products"}, headers=org.h("viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "answer": "You have 3 products.",
        "mode": "claude",
        "tool_calls": [{"tool": "list_products", "ok": True}],
        "notice": None,
    }
    first, second = msgs.calls
    assert first["model"] == get_settings().anthropic_model
    assert first["fallbacks"] == "default" and first["betas"] == [assistant_service.FALLBACK_BETA]
    assert {t["name"] for t in first["tools"]} == set(assistant_service.TOOLS)
    result = second["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1"
    data = json.loads(result["content"])
    assert data["total_matching"] == 3  # the limit is clipped and only the caller's products are visible
    assert "OTHER-ORG-SECRET" not in result["content"]


def test_unknown_tool_returns_error_result(client, org, with_key, monkeypatch):
    fc, msgs = fake_client(
        NS(stop_reason="tool_use", content=[tool_use("delete_everything")]),
        NS(stop_reason="end_turn", content=[text("I can't do that.")]),
    )
    monkeypatch.setattr(assistant_service, "_client", lambda timeout_s: fc)
    body = client.post("/chat", json={"message": "delete all"}, headers=org.h()).json()
    assert body["tool_calls"] == [{"tool": "delete_everything", "ok": False}]
    res = msgs.calls[1]["messages"][-1]["content"][0]
    assert res["is_error"] is True and res["content"] == "Unknown tool"


def test_tool_permission_is_rechecked(monkeypatch, db, org):
    from app.api.deps import Principal
    from app.core.errors import Forbidden
    from app.core.security import Perm

    monkeypatch.setitem(
        assistant_service.TOOLS,
        "admin_only",
        assistant_service.Tool("admin_only", "x", {"type": "object"}, Perm.manage_users, lambda *_: {}),
    )
    viewer = Principal(user_id=org.users["viewer"], org_id=org.org_id, role="viewer", email="v@x", ip=None)
    assert "admin_only" not in {t["name"] for t in assistant_service.tools_for(viewer)}
    with pytest.raises(Forbidden):
        assistant_service.run_tool(db, viewer, "admin_only", {})


@pytest.mark.parametrize(
    "outcome,notice",
    [
        (NS(stop_reason="refusal", content=[]), "declined"),
        (anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com")), "unavailable"),
        (NS(stop_reason="end_turn", content=[]), "no answer"),
    ],
)
def test_failures_fall_back_to_templates(client, org, with_key, monkeypatch, outcome, notice):
    fc, _ = fake_client(outcome)
    monkeypatch.setattr(assistant_service, "_client", lambda timeout_s: fc)
    body = client.post("/chat", json={"message": "any alerts?"}, headers=org.h()).json()
    assert body["mode"] == "fallback" and notice in body["notice"]
    assert body["answer"].startswith("There are no open alerts")


def test_tool_loop_is_bounded(client, org, with_key, monkeypatch):
    loop = [NS(stop_reason="tool_use", content=[tool_use("get_alerts", id_=f"t{i}")]) for i in range(10)]
    fc, msgs = fake_client(*loop)
    monkeypatch.setattr(assistant_service, "_client", lambda timeout_s: fc)
    body = client.post("/chat", json={"message": "alerts"}, headers=org.h()).json()
    assert body["mode"] == "fallback" and "too many steps" in body["notice"]
    assert len(msgs.calls) == assistant_service.MAX_TOOL_ROUNDS + 1


def test_history_is_normalised(client, org, with_key, monkeypatch):
    fc, msgs = fake_client(NS(stop_reason="end_turn", content=[text("ok")]))
    monkeypatch.setattr(assistant_service, "_client", lambda timeout_s: fc)
    hist = [
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"},
        {"role": "user", "content": "dangling"},
    ]
    client.post("/chat", json={"message": "q", "history": hist}, headers=org.h())
    sent = msgs.calls[0]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[0]["content"] == "a\n\nb" and sent[-1]["content"] == "q"


def test_validation_auth_and_rate_limit(client, org):
    assert client.post("/chat", json={"message": "hi"}).status_code == 401
    assert client.post("/chat", json={"message": ""}, headers=org.h()).status_code == 422
    assert client.post("/chat", json={"message": "x" * 2001}, headers=org.h()).status_code == 422
    codes = [client.post("/chat", json={"message": "alerts"}, headers=org.h()).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
