"""Operational commands.

python -m app.cli bootstrap            # create the first organisation + admin (idempotent)
python -m app.cli seed-demo            # DEMO ONLY: load synthetic sales + inventory into the admin's org
python -m app.cli create-user EMAIL ROLE
python -m app.cli purge-expired          # delete expired refresh tokens, OAuth states, old webhook ids
python -m app.cli reencrypt-tokens       # re-encrypt Shopify tokens with the newest TOKEN_ENCRYPTION_KEYS key
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
from app.services import auth_service, dataset_service, maintenance, shopify_service


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
                p.on_hand = rng.randint(0, 900)
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
    else:
        create_user(a.email, a.role)


if __name__ == "__main__":
    main()
