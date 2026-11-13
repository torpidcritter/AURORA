"""Team invitations and the getting-started checklist (ADR-041)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import AuditEvent, Invitation, User
from app.services import maintenance

NEW_PW = "Invited-pass-123"


def _token(url: str) -> str:
    assert "/accept-invite#token=" in url
    return url.split("#token=", 1)[1]


def test_invite_accept_and_sign_in(client, org, db):
    r = client.post("/users/invites", json={"email": "New.Person@example.com", "role": "operator"}, headers=org.h())
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["email"] == "new.person@example.com" and inv["state"] == "pending" and inv["emailed"] is False
    token = _token(inv["invite_url"])
    stored = db.scalars(select(Invitation)).one()
    assert token not in stored.token_hash  # only the hash is kept

    info = client.post("/auth/invite/inspect", json={"token": token}).json()
    assert info == {
        "email": "new.person@example.com",
        "role": "operator",
        "org_name": "Acme",
        "expires_at": inv["expires_at"],
    }
    weak = client.post("/auth/invite/accept", json={"token": token, "password": "short"})
    assert weak.status_code == 422
    r = client.post("/auth/invite/accept", json={"token": token, "full_name": "New Person", "password": NEW_PW})
    assert r.status_code == 201, r.text
    user = db.scalars(select(User).where(User.email == "new.person@example.com")).one()
    assert user.org_id == org.org_id and user.role == "operator" and user.full_name == "New Person"
    assert client.post("/auth/login", json={"email": "new.person@example.com", "password": NEW_PW}).status_code == 200

    again = client.post("/auth/invite/accept", json={"token": token, "password": NEW_PW})
    assert again.status_code == 400 and again.json()["error"]["code"] == "invalid_invitation"
    listed = client.get("/users/invites", headers=org.h()).json()
    assert listed[0]["state"] == "accepted"
    actions = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.org_id == org.org_id))]
    assert "user.invited" in actions and "user.invite_accepted" in actions
    # The address now has an account, so it cannot be invited again.
    dup = client.post("/users/invites", json={"email": "new.person@example.com"}, headers=org.h())
    assert dup.status_code == 409


def test_reinvite_revoke_expiry_and_isolation(client, org, other_org, db):
    first = client.post("/users/invites", json={"email": "x@example.com"}, headers=org.h()).json()
    second = client.post("/users/invites", json={"email": "x@example.com", "role": "analyst"}, headers=org.h()).json()
    bad = client.post("/auth/invite/inspect", json={"token": _token(first["invite_url"])})
    assert bad.status_code == 400  # replaced by the second invitation
    assert client.post("/auth/invite/inspect", json={"token": _token(second["invite_url"])}).json()["role"] == "analyst"

    assert client.delete(f"/users/invites/{second['id']}", headers=other_org.h()).status_code == 404
    assert client.get("/users/invites", headers=other_org.h()).json() == []
    assert client.delete(f"/users/invites/{second['id']}", headers=org.h()).status_code == 204
    assert client.post("/auth/invite/inspect", json={"token": _token(second["invite_url"])}).status_code == 400

    third = client.post("/users/invites", json={"email": "y@example.com"}, headers=org.h()).json()
    inv = db.get(Invitation, __import__("uuid").UUID(third["id"]))
    inv.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    r = client.post("/auth/invite/accept", json={"token": _token(third["invite_url"]), "password": NEW_PW})
    assert r.status_code == 400
    assert {i["state"] for i in client.get("/users/invites", headers=org.h()).json()} == {"revoked", "expired"}

    # Rows go 90 days after they stopped working.
    inv.expires_at = datetime.now(UTC) - timedelta(days=91)
    db.commit()
    assert maintenance.purge_expired(db)["invitations"] == 1


def test_invite_email_when_smtp_is_configured(client, org, monkeypatch):
    sent = []
    monkeypatch.setattr(get_settings(), "mail_backend", "smtp")
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.com")
    monkeypatch.setattr(
        "app.services.mailer.send", lambda to, subject, text, html=None: sent.append((to, subject, text))
    )
    r = client.post("/users/invites", json={"email": "mail@example.com"}, headers=org.h()).json()
    assert r["emailed"] is True
    assert sent[0][0] == ["mail@example.com"] and "Acme" in sent[0][1] and r["invite_url"] in sent[0][2]


def test_onboarding_checklist(client, org, db):
    got = client.get("/onboarding", headers=org.h("viewer")).json()
    steps = {s["key"]: s["done"] for s in got["steps"]}
    assert steps["team"] is True  # the fixture organisation has four users
    assert steps["sales"] is False and steps["supplier"] is False and got["total"] == 7 and not got["complete"]
    client.post("/suppliers", json={"name": "Acme Supply"}, headers=org.h())
    got = client.get("/onboarding", headers=org.h("viewer")).json()
    assert {s["key"]: s["done"] for s in got["steps"]}["supplier"] is True
    assert got["done"] == sum(1 for s in got["steps"] if s["done"])
