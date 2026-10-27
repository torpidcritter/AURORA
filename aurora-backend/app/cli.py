"""Operational commands.

python -m app.cli bootstrap            # create the first organisation + admin (idempotent)
python -m app.cli seed-demo            # DEMO ONLY: load synthetic sales + inventory into the admin's org
python -m app.cli create-user EMAIL ROLE
python -m app.cli purge-expired          # delete expired refresh tokens, OAuth states, old webhook ids
python -m app.cli reencrypt-tokens       # re-encrypt Shopify tokens with the newest TOKEN_ENCRYPTION_KEYS key
python -m app.cli grant-app-role         # as the schema owner, after migrations: create/refresh the runtime DB role
python -m app.cli predeploy              # platform release hook: migrate + grant using MIGRATION_DATABASE_URL
"""

from __future__ import annotations

import argparse
import getpass
import random
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Organization, Product, User
from app.db.session import session_scope
from app.services import auth_service, dataset_service, maintenance, shopify_service, stock_service


def bootstrap() -> None:
    s = get_settings()
    pw = s.bootstrap_admin_password.get_secret_value() if s.bootstrap_admin_password else None
    if not pw:
        sys.exit("Set BOOTSTRAP_ADMIN_PASSWORD (it is used once and never stored in plain text).")
    with session_scope() as db:
        existing = db.scalar(select(User).where(User.email == s.bootstrap_admin_email.lower()))
        if existing:
            print(f"Admin {existing.email} already exists; nothing to do.")
            return
        org = Organization(name=s.bootstrap_org_name)
        db.add(org)
        db.flush()
        auth_service.create_user(
            db, org_id=org.id, email=s.bootstrap_admin_email, password=pw, role="admin", full_name="Administrator"
        )
        print(f"Created organisation '{org.name}' and admin {s.bootstrap_admin_email}.")


def seed_demo(products: int, days: int) -> None:
    from app.devtools.synthetic import generate_sales, to_csv_bytes

    s = get_settings()
    with session_scope() as db:
        admin = db.scalar(select(User).where(User.email == s.bootstrap_admin_email.lower()))
        if admin is None:
            sys.exit("Run bootstrap first.")
        from datetime import date

        df = generate_sales(n_products=products, days=days, end=date.today(), seed=11)
        out = dataset_service.ingest_upload(
            db,
            org_id=admin.org_id,
            user_id=admin.id,
            filename="DEMO-synthetic-sales.csv",
            data=to_csv_bytes(df),
            tags={"demo": "true"},
            missing_policy="zero",
        )
        rng = random.Random(5)
        for p in db.scalars(select(Product).where(Product.org_id == admin.org_id)):
            if not p.inventory_known:
                stock_service.set_level(
                    db, p, stock_service.default_location(db, admin.org_id).id, on_hand=rng.randint(0, 900)
                )
                p.lead_time_days = rng.choice([3, 5, 7, 10, 14])
                p.unit_cost = round(rng.uniform(1.5, 40), 2)
                p.inventory_known = True
        print(f"Demo dataset {'already present' if out.duplicate else 'loaded'}: {out.dataset.accepted_rows} rows.")


def create_user(email: str, role: str) -> None:
    pw = getpass.getpass("Password: ")
    with session_scope() as db:
        admin = db.scalar(select(User).where(User.role == "admin").order_by(User.created_at))
        if admin is None:
            sys.exit("Run bootstrap first.")
        auth_service.create_user(db, org_id=admin.org_id, email=email, password=pw, role=role)
        print(f"Created {role} {email}.")


def grant_app_role() -> None:
    from app.db.roles import grant_app_role as grant
    from app.db.session import get_engine

    s = get_settings()
    if s.app_db_password is None:
        sys.exit("Set APP_DB_PASSWORD (at least 16 characters) for the application database role.")
    grant(get_engine(), s.app_db_role, s.app_db_password.get_secret_value())
    print(f"Role {s.app_db_role} can read and write rows; it cannot change the schema.")


def predeploy() -> None:
    """Release hook for platforms such as Railway: migrations and grants as the owner, then the app starts."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from app.core.config import normalise_db_url
    from app.db.roles import grant_app_role as grant

    s = get_settings()
    url = normalise_db_url(s.migration_database_url.get_secret_value()) if s.migration_database_url else s.database_url
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")
    if s.app_db_password is None:
        print("APP_DB_PASSWORD is not set: the application keeps using the owner login (see docs/RUNBOOK.md).")
        return
    engine = create_engine(url)
    try:
        grant(engine, s.app_db_role, s.app_db_password.get_secret_value())
    finally:
        engine.dispose()
    print(f"Migrated; role {s.app_db_role} refreshed.")


def main() -> None:
    ap = argparse.ArgumentParser(prog="aurora")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap")
    d = sub.add_parser("seed-demo")
    d.add_argument("--products", type=int, default=12)
    d.add_argument("--days", type=int, default=240)
    u = sub.add_parser("create-user")
    u.add_argument("email")
    u.add_argument("role", choices=["viewer", "operator", "analyst", "admin"])
    sub.add_parser("purge-expired")
    sub.add_parser("reencrypt-tokens")
    sub.add_parser("grant-app-role")
    sub.add_parser("predeploy")
    a = ap.parse_args()
    if a.cmd == "bootstrap":
        bootstrap()
    elif a.cmd == "seed-demo":
        seed_demo(a.products, a.days)
    elif a.cmd == "purge-expired":
        with session_scope() as db:
            print(maintenance.purge_expired(db))
    elif a.cmd == "reencrypt-tokens":
        with session_scope() as db:
            res = shopify_service.reencrypt_tokens(db)
        print(f"Re-encrypted {res['rotated']} token(s).")
        if res["failed"]:
            print("Could not decrypt (reconnect these shops): " + ", ".join(res["failed"]), file=sys.stderr)
            sys.exit(1)
    elif a.cmd == "grant-app-role":
        grant_app_role()
    elif a.cmd == "predeploy":
        predeploy()
    else:
        create_user(a.email, a.role)


if __name__ == "__main__":
    main()
