"""Two-factor login with TOTP and recovery codes, and the organisation requirement (ADR-040)."""

from __future__ import annotations

from sqlalchemy import select

from app.core import crypto, totp
from app.core.config import get_settings
from app.db.models import AuditEvent, RefreshToken, User
from tests.conftest import PASSWORD


def _login(client, email, otp=None):
    body = {"email": email, "password": PASSWORD}
    if otp is not None:
        body["otp"] = otp
    return client.post("/auth/login", json=body)


def _enrol(client, org, role="operator"):
    h = org.h(role)
    r = client.post("/auth/two-factor/setup", json={"current_password": PASSWORD}, headers=h)
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/Aurora:")
    r = client.post("/auth/two-factor/enable", json={"code": totp.code_at(secret, totp.current_step() - 1)}, headers=h)
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def _code(secret, offset=0):
    return totp.code_at(secret, totp.current_step() + offset)


def test_enrol_and_sign_in_with_a_code(client, org, db):
    email = org.emails["operator"]
    h = org.h("operator")
    assert client.post("/auth/two-factor/setup", json={"current_password": "nope"}, headers=h).status_code == 422
    secret, codes = _enrol(client, org)
    assert len(codes) == 10 and all(len(c) == 11 and c[5] == "-" for c in codes)

    user = db.get(User, org.users["operator"])
    db.refresh(user)
    assert user.totp_secret and secret not in user.totp_secret and crypto.decrypt(user.totp_secret) == secret
    assert all(c.replace("-", "") not in str(user.recovery_codes) for c in codes)  # stored hashed

    status = client.get("/auth/two-factor", headers=h).json()
    assert status["enabled"] and status["recovery_codes_left"] == 10 and not status["required_by_organisation"]
    assert client.get("/users", headers=org.h()).json()["items"][1]["two_factor_enabled"] in (True, False)

    r = _login(client, email)
    assert r.status_code == 401 and r.json()["error"]["code"] == "two_factor_required"
    r = _login(client, email, "000000" if _code(secret) != "000000" else "111111")
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_two_factor_code"
    code = _code(secret)
    assert _login(client, email, code).status_code == 200
    assert _login(client, email, code).status_code == 401  # the same code works once
    assert _login(client, email, _code(secret, 1)).status_code == 200  # the next step is accepted (clock drift)

    failures = db.scalars(
        select(AuditEvent).where(AuditEvent.actor_email == email, AuditEvent.outcome == "failure")
    ).all()
    assert {e.details["reason"] for e in failures} == {"bad_second_factor"}
    # Setup cannot be restarted while it is on.
    assert client.post("/auth/two-factor/setup", json={"current_password": PASSWORD}, headers=h).status_code == 409


def test_recovery_codes_are_single_use_and_replaceable(client, org, db):
    email = org.emails["operator"]
    secret, codes = _enrol(client, org)
    assert _login(client, email, codes[0].upper()).status_code == 200  # case and dash do not matter
    assert _login(client, email, codes[0]).status_code == 401
    used = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.recovery_code_used")).one()
    assert used.details["remaining"] == 9

    h = org.h("operator")
    r = client.post("/auth/two-factor/recovery-codes", json={"code": _code(secret)}, headers=h)
    assert r.status_code == 200
    fresh = r.json()["recovery_codes"]
    assert _login(client, email, codes[1]).status_code == 401  # old codes stop working
    assert _login(client, email, fresh[0]).status_code == 200


def test_turning_it_off_needs_password_and_code(client, org, db):
    email = org.emails["operator"]
    secret, _ = _enrol(client, org)
    h = org.h("operator")
    bad = client.post("/auth/two-factor/disable", json={"current_password": PASSWORD, "code": "12345678"}, headers=h)
    assert bad.status_code == 422
    r = client.post("/auth/two-factor/disable", json={"current_password": PASSWORD, "code": _code(secret)}, headers=h)
    assert r.status_code == 204
    assert _login(client, email).status_code == 200
    actions = {e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.org_id == org.org_id))}
    assert {"auth.two_factor_enabled", "auth.two_factor_disabled"} <= actions


def test_repeated_bad_codes_lock_the_account(client, org, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_lockout_threshold", 3)
    email = org.emails["viewer"]
    secret, _ = _enrol(client, org, "viewer")
    wrong = next(c for c in ("000000", "111111", "222222") if c not in {_code(secret, d) for d in (-1, 0, 1)})
    statuses = [_login(client, email, wrong).json()["error"]["code"] for _ in range(4)]
    assert "account_locked" in statuses
    assert _login(client, email, _code(secret)).json()["error"]["code"] == "account_locked"


def test_organisation_requirement_and_admin_reset(client, org, other_org, db):
    admin, viewer = org.h("admin"), org.h("viewer")
    r = client.put("/auth/two-factor/policy", json={"required": True}, headers=admin)
    assert r.status_code == 409  # the admin has not set it up yet
    _enrol(client, org, "admin")
    assert client.put("/auth/two-factor/policy", json={"required": True}, headers=admin).status_code == 200

    # A viewer without it can only reach their account and the setup.
    r = client.get("/products", headers=viewer)
    assert r.status_code == 403 and r.json()["error"]["code"] == "two_factor_required"
    me = client.get("/auth/me", headers=viewer).json()
    assert me["two_factor_required"] is True
    assert client.get("/auth/two-factor", headers=viewer).json()["required_by_organisation"] is True
    assert client.get("/products", headers=other_org.h("viewer")).status_code == 200  # other organisations unaffected
    secret, _ = _enrol(client, org, "viewer")
    assert client.get("/products", headers=viewer).status_code == 200
    r = client.post(
        "/auth/two-factor/disable", json={"current_password": PASSWORD, "code": _code(secret)}, headers=viewer
    )
    assert r.status_code == 409  # cannot be turned off while required

    # The admin resets the viewer's lost device: sessions end and sign-in works with the password alone again.
    viewer_id = org.users["viewer"]
    assert client.post(f"/users/{org.users['admin']}/two-factor/reset", headers=admin).status_code == 403
    assert client.post(f"/users/{viewer_id}/two-factor/reset", headers=other_org.h()).status_code == 404
    assert client.post(f"/users/{viewer_id}/two-factor/reset", headers=admin).status_code == 204
    assert client.get("/auth/me", headers=viewer).status_code == 401
    assert all(t.revoked_at for t in db.scalars(select(RefreshToken).where(RefreshToken.user_id == viewer_id)))
    assert _login(client, org.emails["viewer"]).status_code == 200

    assert client.put("/auth/two-factor/policy", json={"required": False}, headers=admin).status_code == 200
    events = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.org_id == org.org_id))]
    assert events.count("settings.two_factor_policy") == 2 and "user.two_factor_reset" in events
