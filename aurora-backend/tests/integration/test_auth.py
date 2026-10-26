from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings
from tests.conftest import PASSWORD


def _login(client, email, pw=PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": pw})


def test_login_me_and_bad_password(client, org):
    r = _login(client, org.emails["analyst"])
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 30 * 60
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["role"] == "analyst"
    assert "train_models" in me.json()["permissions"]
    assert "manage_users" not in me.json()["permissions"]

    bad = _login(client, org.emails["analyst"], "wrong-password")
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "unauthorized"
    unknown = _login(client, "nobody@example.com")
    assert unknown.status_code == 401
    # The spec's username/password wording: `username` is accepted as the email.
    alias = client.post("/auth/login", json={"username": org.emails["viewer"], "password": PASSWORD})
    assert alias.status_code == 200 and alias.json()["access_token"]
    assert unknown.json()["error"]["message"] == bad.json()["error"]["message"]  # no user enumeration


def test_protected_requires_token(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_expired_and_tampered_tokens_rejected(client, org):
    s = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(org.users["admin"]),
        "org": str(org.org_id),
        "role": "admin",
        "tv": 0,
        "typ": "access",
        "iat": int((now - timedelta(hours=2)).timestamp()),
        "exp": int((now - timedelta(hours=1)).timestamp()),
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
    }
    expired = jwt.encode(claims, s.secret_key.get_secret_value(), algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    claims["exp"] = int((now + timedelta(hours=1)).timestamp())
    forged = jwt.encode(claims, "some-other-secret-key-that-is-long-enough", algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    none_alg = jwt.encode(claims, None, algorithm="none")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {none_alg}"}).status_code == 401


def test_refresh_rotation_and_reuse_detection(client, org, db):
    from app.db.models import RefreshToken
    from app.services import auth_service

    t1 = _login(client, org.emails["viewer"]).json()
    r = client.post("/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    assert r.status_code == 200
    t2 = r.json()
    assert t2["refresh_token"] != t1["refresh_token"]

    # Immediate replay inside the grace window: rejected but family intact.
    r = client.post("/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    assert r.status_code == 401 and r.json()["error"]["code"] == "refresh_superseded"
    assert client.post("/auth/refresh", json={"refresh_token": t2["refresh_token"]}).status_code == 200

    # Replay after the grace window: whole family revoked.
    t3 = _login(client, org.emails["viewer"]).json()
    t4 = client.post("/auth/refresh", json={"refresh_token": t3["refresh_token"]}).json()
    tok = (
        db.query(RefreshToken).filter(RefreshToken.used_at.isnot(None)).order_by(RefreshToken.created_at.desc()).first()
    )
    tok.used_at = datetime.now(UTC) - timedelta(seconds=auth_service.REUSE_GRACE_SECONDS + 5)
    db.commit()
    r = client.post("/auth/refresh", json={"refresh_token": t3["refresh_token"]})
    assert r.status_code == 401 and r.json()["error"]["code"] == "token_reuse"
    assert client.post("/auth/refresh", json={"refresh_token": t4["refresh_token"]}).status_code == 401


def test_logout_revokes_refresh(client, org):
    t = _login(client, org.emails["viewer"]).json()
    assert client.post("/auth/logout", json={"refresh_token": t["refresh_token"]}).status_code == 204
    assert client.post("/auth/refresh", json={"refresh_token": t["refresh_token"]}).status_code == 401
    # idempotent
    assert client.post("/auth/logout", json={"refresh_token": t["refresh_token"]}).status_code == 204


def test_account_lockout(client, org):
    s = get_settings()
    for _ in range(s.login_lockout_threshold):
        assert _login(client, org.emails["operator"], "nope-nope").status_code in (401, 429)
    r = _login(client, org.emails["operator"])
    assert r.status_code in (401, 429)
    if r.status_code == 401:
        assert r.json()["error"]["code"] == "account_locked"


def test_login_rate_limit(client, org):
    s = get_settings()
    codes = [_login(client, org.emails["viewer"], "bad").status_code for _ in range(s.login_rate_limit_per_minute + 3)]
    assert 429 in codes


def test_register_requires_admin_and_strong_password(client, org):
    body = {"email": "new.user@example.com", "password": "Another-Str0ng-pw", "role": "operator"}
    assert client.post("/auth/register", json=body, headers=org.h("analyst")).status_code == 403
    r = client.post("/auth/register", json=body, headers=org.h("admin"))
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "operator"
    assert client.post("/auth/register", json=body, headers=org.h("admin")).status_code == 409
    weak = {**body, "email": "weak@example.com", "password": "alllowercase"}
    assert client.post("/auth/register", json=weak, headers=org.h("admin")).status_code == 422


def test_role_change_invalidates_existing_tokens(client, org):
    viewer_headers = org.h("viewer")
    assert client.get("/auth/me", headers=viewer_headers).status_code == 200
    r = client.patch(f"/users/{org.users['viewer']}", json={"role": "operator"}, headers=org.h("admin"))
    assert r.status_code == 200
    assert client.get("/auth/me", headers=viewer_headers).status_code == 401


def test_deactivated_user_cannot_login(client, org):
    client.patch(f"/users/{org.users['viewer']}", json={"is_active": False}, headers=org.h("admin"))
    assert _login(client, org.emails["viewer"]).status_code == 401


def test_admin_cannot_demote_self(client, org):
    r = client.patch(f"/users/{org.users['admin']}", json={"role": "viewer"}, headers=org.h("admin"))
    assert r.status_code == 403


def test_users_are_tenant_scoped(client, org, other_org):
    r = client.get("/users", headers=org.h("admin"))
    emails = {u["email"] for u in r.json()["items"]}
    assert other_org.emails["admin"] not in emails
    r = client.patch(f"/users/{other_org.users['viewer']}", json={"role": "admin"}, headers=org.h("admin"))
    assert r.status_code == 404


def test_password_change_revokes_sessions(client, org):
    t = _login(client, org.emails["analyst"]).json()
    h = {"Authorization": f"Bearer {t['access_token']}"}
    r = client.post(
        "/auth/password", json={"current_password": PASSWORD, "new_password": "Brand-New-pw-123"}, headers=h
    )
    assert r.status_code == 204
    assert client.get("/auth/me", headers=h).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": t["refresh_token"]}).status_code == 401
    assert _login(client, org.emails["analyst"], "Brand-New-pw-123").status_code == 200


def test_audit_records_login(client, org, db):
    from app.db.models import AuditEvent

    _login(client, org.emails["viewer"])
    _login(client, org.emails["viewer"], "bad")
    rows = db.query(AuditEvent).filter(AuditEvent.action == "auth.login").all()
    assert {r.outcome for r in rows} >= {"success", "failure"}
    assert all("password" not in str(r.details) for r in rows)


def test_client_address_from_bff_needs_the_shared_secret(client, org, db, monkeypatch):
    from pydantic import SecretStr

    from app.db.models import AuditEvent

    monkeypatch.setattr(get_settings(), "bff_shared_secret", SecretStr("bff-shared-secret-0123456789abcdef"))

    def login_from(ip: str, secret: str) -> int:
        headers = {"x-aurora-client-ip": ip, "x-aurora-bff": secret}
        return client.post(
            "/auth/login", json={"email": org.emails["viewer"], "password": "bad"}, headers=headers
        ).status_code

    login_from("203.0.113.9", "bff-shared-secret-0123456789abcdef")
    login_from("198.51.100.4", "forged")
    login_from("not an address", "bff-shared-secret-0123456789abcdef")
    ips = [r.ip for r in db.query(AuditEvent).filter(AuditEvent.action == "auth.login").order_by(AuditEvent.id)]
    assert ips == ["203.0.113.9", "testclient", "testclient"]


def test_rate_limit_is_per_reported_client(client, org, monkeypatch):
    from pydantic import SecretStr

    secret = "bff-shared-secret-0123456789abcdef"
    monkeypatch.setattr(get_settings(), "bff_shared_secret", SecretStr(secret))
    per_ip = get_settings().login_rate_limit_per_minute * 3

    def status(ip: str, email: str) -> int:
        headers = {"x-aurora-client-ip": ip, "x-aurora-bff": secret}
        return client.post("/auth/login", json={"email": email, "password": "bad"}, headers=headers).status_code

    # Different e-mail addresses each time, so only the per-address limit can trip.
    codes = [status("203.0.113.9", f"guess{i}@example.com") for i in range(per_ip + 2)]
    assert codes[0] == 401 and codes[-1] == 429
    # Another user behind the same BFF is not blocked by the first client's attempts.
    assert status("198.51.100.4", "someone@example.com") == 401
