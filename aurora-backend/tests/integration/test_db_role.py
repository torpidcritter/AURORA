"""The runtime database role can read and write rows but cannot change the schema or rewrite history."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from app.db.roles import drop_app_role, grant_app_role
from app.db.session import get_engine

APP_PW = "app-role-test-password-0123"


@pytest.fixture
def app_engine() -> Iterator:  # type: ignore[type-arg]
    owner = get_engine()
    with owner.connect() as c:
        if not c.execute(text("SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user")).scalar():
            pytest.fail("the test database user needs CREATEROLE to exercise the application role")
    role = f"aurora_app_t{uuid.uuid4().hex[:8]}"
    grant_app_role(owner, role, APP_PW)
    eng = create_engine(owner.url.set(username=role, password=APP_PW))
    try:
        yield eng
    finally:
        eng.dispose()
        drop_app_role(owner, role)


def _denied(eng, sql: str, **params) -> bool:  # type: ignore[no-untyped-def]
    try:
        with eng.begin() as c:
            c.execute(text(sql), params)
    except ProgrammingError as exc:
        return isinstance(exc.orig, InsufficientPrivilege)  # "permission denied" or "must be owner"
    return False


def test_app_role_reads_and_writes_rows(app_engine) -> None:  # type: ignore[no-untyped-def]
    oid = uuid.uuid4()
    with app_engine.begin() as c:
        c.execute(text("INSERT INTO organizations (id, name, settings) VALUES (:i, 'RoleCo', '{}')"), {"i": oid})
        c.execute(text("UPDATE organizations SET name = 'RoleCo 2' WHERE id = :i"), {"i": oid})
        assert c.execute(text("SELECT name FROM organizations WHERE id = :i"), {"i": oid}).scalar() == "RoleCo 2"
        c.execute(
            text("INSERT INTO audit_events (org_id, action, outcome) VALUES (:i, 'role.test', 'success')"),
            {"i": oid},
        )
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        c.execute(text("DELETE FROM audit_events WHERE org_id = :i"), {"i": oid})  # retention expiry
        c.execute(text("DELETE FROM organizations WHERE id = :i"), {"i": oid})


def test_app_role_cannot_change_schema_or_history(app_engine) -> None:  # type: ignore[no-untyped-def]
    assert _denied(app_engine, "CREATE TABLE intruder (id int)")
    assert _denied(app_engine, "ALTER TABLE users ADD COLUMN intruder int")
    assert _denied(app_engine, "DROP TABLE sales_records")
    assert _denied(app_engine, "TRUNCATE users")
    assert _denied(app_engine, "UPDATE audit_events SET action = 'rewritten'")
    assert _denied(app_engine, "UPDATE alembic_version SET version_num = '0000'")
    assert _denied(app_engine, "DELETE FROM alembic_version")


def test_grant_is_idempotent_and_refuses_weak_input() -> None:
    owner = get_engine()
    role = f"aurora_app_t{uuid.uuid4().hex[:8]}"
    try:
        grant_app_role(owner, role, APP_PW)
        grant_app_role(owner, role, APP_PW + "-rotated")  # re-run after a migration or a password change
    finally:
        drop_app_role(owner, role)
    with pytest.raises(ValueError, match="at least 16"):
        grant_app_role(owner, role, "short")
    with pytest.raises(ValueError, match="schema owner"):
        grant_app_role(owner, owner.url.username, APP_PW)
