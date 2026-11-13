"""Admin-issued one-time password reset links: issue, use once, expire, revoke sessions, tenant isolation, abuse."""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import redis
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import AuditEvent, PasswordResetToken, User
from tests.conftest import PASSWORD

NEW_PASSWORD = "Fresh-Start-pw-2026"
PREFIX = "/reset-password#token="


def _login(client, email, pw=PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": pw})


def _issue(client, org, role="viewer", target=None):
    user_id = target or org.users[role]
    return client.post(f"/auth/users/{user_id}/reset-link", headers=org.h("admin"))


def _token(resp) -> str:
    url = resp.json()["reset_url"]
    assert PREFIX in url, url
    return url.split(PREFIX, 1)[1]


def _reset(client, token, pw=NEW_PASSWORD, headers=None):
    return client.post("/auth/password-reset", json={"token": token, "new_password": pw}, headers=headers or {})


def _reset_audits(db, outcome=None):
    q = select(AuditEvent).where(AuditEvent.action == "auth.password_reset").order_by(AuditEvent.id)
    if outcome:
        q = q.where(AuditEvent.outcome == outcome)
    return list(db.scalars(q))


def test_issue_and_use_link_revokes_every_session(client, org, db, caplog):
    caplog.set_level(logging.DEBUG)
    session = _login(client, org.emails["viewer"]).json()
    old_access = {"Authorization": f"Bearer {session['access_token']}"}

    r = _issue(client, org)
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"reset_url", "expires_at"}
    assert body["reset_url"].startswith(get_settings().frontend_url.rstrip("/") + PREFIX)
    token = _token(r)
    assert len(token) >= 40
    expires_at = datetime.fromisoformat(body["expires_at"])
    ttl = timedelta(minutes=get_settings().password_reset_ttl_minutes)
    assert abs((expires_at - datetime.now(UTC)) - ttl) < timedelta(minutes=1)

    # Only the hash is stored.
    row = db.scalars(select(PasswordResetToken)).one()
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.user_id == org.users["viewer"] and row.org_id == org.org_id and row.created_by == org.users["admin"]
    assert row.used_at is None

    r = _reset(client, token)
    assert r.status_code == 204, r.text

    # Old access token, old refresh token and old password all stop working; the new password works.
    assert client.get("/auth/me", headers=old_access).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": session["refresh_token"]}).status_code == 401
    assert _login(client, org.emails["viewer"]).status_code == 401
    assert _login(client, org.emails["viewer"], NEW_PASSWORD).status_code == 200
    db.expire_all()
    assert db.get(PasswordResetToken, row.id).used_at is not None
    assert db.get(User, org.users["viewer"]).token_version == 1

    created = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.reset_link_created")).one()
    assert created.actor_user_id == org.users["admin"] and created.resource_id == str(org.users["viewer"])
    assert created.org_id == org.org_id and created.outcome == "success"
    [done] = _reset_audits(db)
    assert done.outcome == "success" and done.actor_user_id == org.users["viewer"]
    assert done.org_id == org.org_id and done.details == {"issued_by": str(org.users["admin"])}

    # The token is never written to the audit log or to the application logs.
    for ev in db.scalars(select(AuditEvent)):
        assert token not in repr({c.name: getattr(ev, c.name) for c in AuditEvent.__table__.columns})
    assert token not in caplog.text


def test_link_is_single_use(client, org, db):
    token = _token(_issue(client, org))
    assert _reset(client, token).status_code == 204
    again = _reset(client, token, pw="Another-Fresh-pw-1")
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "invalid_reset_token"
    assert _login(client, org.emails["viewer"], NEW_PASSWORD).status_code == 200
    assert _reset_audits(db, "failure")[-1].details == {"reason": "used"}


def test_unknown_expired_and_used_links_get_the_same_answer(client, org, db):
    unknown = _reset(client, "x" * 43)
    token = _token(_issue(client, org))
    row = db.scalars(select(PasswordResetToken)).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    expired = _reset(client, token)
    assert unknown.status_code == expired.status_code == 400
    assert unknown.json()["error"]["message"] == expired.json()["error"]["message"]
    assert unknown.json()["error"]["code"] == expired.json()["error"]["code"] == "invalid_reset_token"
    assert _login(client, org.emails["viewer"]).status_code == 200  # password unchanged

    failures = _reset_audits(db, "failure")
    assert [f.details["reason"] for f in failures] == ["unknown_token", "expired"]
    assert failures[0].org_id is None and failures[1].org_id == org.org_id
    assert failures[1].resource_id == str(org.users["viewer"])


def test_new_link_supersedes_the_previous_one(client, org):
    first = _token(_issue(client, org))
    second = _token(_issue(client, org))
    assert first != second
    assert _reset(client, first).status_code == 400
    assert _reset(client, second).status_code == 204


def test_concurrent_use_succeeds_exactly_once(client, org, db):
    token = _token(_issue(client, org))
    n = 6
    barrier = threading.Barrier(n)
    codes: list[int] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        barrier.wait()
        code = _reset(client, token, pw=f"Racing-pw-{i}-xyz").status_code
        with lock:
            codes.append(code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(codes) == [204] + [400] * (n - 1), codes
    assert len(_reset_audits(db, "success")) == 1
    # Exactly one of the racing passwords was set.
    winners = [i for i in range(n) if _login(client, org.emails["viewer"], f"Racing-pw-{i}-xyz").status_code == 200]
    assert len(winners) == 1
    assert db.get(User, org.users["viewer"]).token_version == 1


def test_claim_waits_for_a_concurrent_transaction(client, org, db):
    """The conditional claim is the single-use guard: a request that reaches it while another transaction has claimed
    the link (uncommitted) waits, then finds the link spent."""
    from app.db.session import get_sessionmaker

    token = _token(_issue(client, org))
    holder = get_sessionmaker()()
    try:
        # A claim in flight that has not committed yet (it holds only the token row).
        holder.execute(
            PasswordResetToken.__table__.update()
            .where(PasswordResetToken.token_hash == hashlib.sha256(token.encode()).hexdigest())
            .values(used_at=datetime.now(UTC))
        )
        result: dict[str, int] = {}
        t = threading.Thread(target=lambda: result.setdefault("code", _reset(client, token).status_code))
        t.start()
        t.join(timeout=1.0)
        assert t.is_alive(), "the second request must block on the row lock"
        holder.commit()
        t.join(timeout=30)
        assert result["code"] == 400
    finally:
        holder.close()
    assert _login(client, org.emails["viewer"]).status_code == 200


def test_cross_tenant_admin_gets_404(client, org, other_org, db):
    r = client.post(f"/auth/users/{other_org.users['viewer']}/reset-link", headers=org.h("admin"))
    assert r.status_code == 404
    assert client.post(f"/auth/users/{uuid.uuid4()}/reset-link", headers=org.h("admin")).status_code == 404
    assert db.scalars(select(PasswordResetToken)).first() is None


@pytest.mark.parametrize("role", ["viewer", "operator", "analyst"])
def test_non_admins_are_forbidden(client, org, db, role):
    r = client.post(f"/auth/users/{org.users['viewer']}/reset-link", headers=org.h(role))
    assert r.status_code == 403
    assert client.post(f"/auth/users/{org.users['viewer']}/reset-link").status_code == 401
    assert db.scalars(select(PasswordResetToken)).first() is None


def test_admin_cannot_issue_a_link_for_their_own_account(client, org):
    r = client.post(f"/auth/users/{org.users['admin']}/reset-link", headers=org.h("admin"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "own_account"


def test_inactive_users_are_refused_and_deactivation_voids_links(client, org, db):
    token = _token(_issue(client, org))
    admin = org.h("admin")
    assert client.patch(f"/users/{org.users['viewer']}", json={"is_active": False}, headers=admin).status_code == 200
    r = _issue(client, org)
    assert r.status_code == 409 and r.json()["error"]["code"] == "user_inactive"
    assert _reset(client, token).status_code == 400
    # Reactivation does not bring the old link back.
    assert client.patch(f"/users/{org.users['viewer']}", json={"is_active": True}, headers=admin).status_code == 200
    assert _reset(client, token).status_code == 400
    assert _login(client, org.emails["viewer"]).status_code == 200


def test_password_change_voids_outstanding_links(client, org):
    token = _token(_issue(client, org))
    r = client.post(
        "/auth/password",
        json={"current_password": PASSWORD, "new_password": "Changed-Myself-pw-1"},
        headers=org.h("viewer"),
    )
    assert r.status_code == 204
    assert _reset(client, token).status_code == 400


def test_weak_password_is_rejected_and_the_link_stays_usable(client, org, db):
    token = _token(_issue(client, org))
    weak = _reset(client, token, pw="alllowercase-only")
    assert weak.status_code == 422
    assert weak.json()["error"]["code"] == "validation_error"
    assert weak.json()["error"]["details"]
    too_short = _reset(client, token, pw="Sh0rt!")
    assert too_short.status_code == 422
    assert _reset_audits(db, "failure")[-1].details == {"reason": "weak_password"}
    assert _reset(client, token).status_code == 204


@pytest.mark.parametrize(
    "body",
    [{}, {"token": "abc", "new_password": NEW_PASSWORD}, {"token": "x" * 300, "new_password": NEW_PASSWORD}],
)
def test_malformed_requests_are_rejected(client, body):
    r = client.post("/auth/password-reset", json=body)
    assert r.status_code == 422
    assert "x" * 300 not in r.text  # input is not echoed back


def test_reset_clears_a_login_lockout(client, org, db):
    user = db.get(User, org.users["viewer"])
    user.locked_until = datetime.now(UTC) + timedelta(minutes=10)
    db.commit()
    assert _reset(client, _token(_issue(client, org))).status_code == 204
    assert _login(client, org.emails["viewer"], NEW_PASSWORD).status_code == 200


def test_reset_attempts_are_rate_limited_per_client(client, org, monkeypatch):
    from pydantic import SecretStr

    secret = "bff-shared-secret-0123456789abcdef"
    monkeypatch.setattr(get_settings(), "bff_shared_secret", SecretStr(secret))
    monkeypatch.setattr(get_settings(), "login_rate_limit_per_minute", 3)

    def attempt(ip: str, token: str = "y" * 43):
        return _reset(client, token, headers={"x-aurora-client-ip": ip, "x-aurora-bff": secret})

    codes = [attempt("203.0.113.9").status_code for _ in range(5)]
    assert codes[0] == 400 and codes[-1] == 429
    limited = attempt("203.0.113.9")
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
    # Another client is not blocked, and can still use a real link.
    assert attempt("198.51.100.4", _token(_issue(client, org))).status_code == 204


def test_rate_limiter_outage_fails_closed_in_production(client, org, monkeypatch):
    from app.core import rate_limit

    class Broken:
        def pipeline(self):
            raise redis.ConnectionError("down")

    token = _token(_issue(client, org))
    monkeypatch.setattr(rate_limit, "get_redis", lambda: Broken())
    monkeypatch.setattr(get_settings(), "environment", "production")
    r = _reset(client, token)
    assert r.status_code == 503
    monkeypatch.setattr(get_settings(), "environment", "test")
    assert _reset(client, token).status_code == 204  # outside production the limiter fails open
