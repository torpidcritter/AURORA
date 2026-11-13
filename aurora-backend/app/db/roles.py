"""Least-privilege runtime database role.

Migrations run as the schema owner. The API and workers connect as a separate login role that can read and write rows
but cannot create, alter or drop anything, and cannot rewrite the audit trail or the migration history. Running
`grant_app_role` after every migration keeps the grants complete when new tables appear; default privileges cover
tables the owner creates later in between.
"""

from __future__ import annotations

from psycopg import sql
from sqlalchemy import Engine

APPEND_ONLY_TABLES = ("audit_events",)  # rows may be inserted and expired by retention, never edited
READ_ONLY_TABLES = ("alembic_version",)


def grant_app_role(engine: Engine, role: str, password: str) -> None:
    """Create or update `role` and grant it DML on the public schema. Must run as the owner of the schema objects."""
    if not role or role.lower() in ("postgres", "public"):
        raise ValueError("refusing to use that role name for the application")
    if len(password) < 16:
        raise ValueError("the application database password must be at least 16 characters")
    r = sql.Identifier(role)
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute("SELECT current_user, current_database()")
            owner, dbname = cur.fetchone()
            if owner == role:
                raise ValueError("run this as the schema owner, not as the application role")
            cur.execute(
                "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls"
                " FROM pg_roles WHERE rolname = %s",
                (role,),
            )
            existing = cur.fetchone()
            if existing is None:
                cur.execute(sql.SQL("CREATE ROLE {} LOGIN").format(r))  # defaults: no superuser, createdb, etc.
            elif existing[0]:
                raise ValueError(f"role {role} has elevated attributes; use a plain login role for the application")
            # DDL cannot take bind parameters; Literal quotes the value safely.
            cur.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(r, sql.Literal(password)))
            cur.execute("SELECT pg_get_userbyid(datdba) = current_user FROM pg_database WHERE datname = %s", (dbname,))
            if cur.fetchone()[0]:  # otherwise rely on the default PUBLIC connect privilege
                cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname), r))
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(r))
            cur.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(r))
            cur.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(r))
            cur.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(r))
            for t in APPEND_ONLY_TABLES:
                if _table_exists(cur, t):
                    cur.execute(sql.SQL("REVOKE UPDATE, TRUNCATE ON {} FROM {}").format(sql.Identifier(t), r))
            for t in READ_ONLY_TABLES:
                if _table_exists(cur, t):
                    cur.execute(
                        sql.SQL("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {} FROM {}").format(sql.Identifier(t), r)
                    )
            o = sql.Identifier(owner)
            cur.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
                ).format(o, r)
            )
            cur.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}"
                ).format(o, r)
            )
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def drop_app_role(engine: Engine, role: str) -> None:
    """Revoke everything `grant_app_role` granted in this database and drop the role (used by tests and teardown)."""
    r = sql.Identifier(role)
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cur.fetchone() is None:
                return
            cur.execute("SELECT current_user, current_database()")
            owner, dbname = cur.fetchone()
            o = sql.Identifier(owner)
            for stmt in (
                "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {r}",
                "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {r}",
                "REVOKE ALL ON SCHEMA public FROM {r}",
                "REVOKE ALL ON DATABASE {d} FROM {r}",
                "ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA public REVOKE ALL ON TABLES FROM {r}",
                "ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {r}",
                "DROP ROLE {r}",
            ):
                cur.execute(sql.SQL(stmt).format(r=r, o=o, d=sql.Identifier(dbname)))
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def _table_exists(cur, name: str) -> bool:  # type: ignore[no-untyped-def]
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{name}",))
    return bool(cur.fetchone()[0])
