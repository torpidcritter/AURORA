"""Organisation export, personal data export, account deletion and organisation deletion (ADR-042)."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, inspect, select, text

from app.core.errors import Conflict
from app.db.models import AuditEvent, Dataset, Job, Organization, Product, User
from app.devtools.synthetic import generate_sales, to_csv_bytes
from app.services import data_rights
from app.storage import get_storage
from tests.conftest import PASSWORD
from tests.integration.test_datasets import upload


def _seed(client, org):
    assert upload(client, org.h("operator"), to_csv_bytes(generate_sales(n_products=2, days=30))).status_code == 201
    client.post("/suppliers", json={"name": "=Evil()"}, headers=org.h())


def test_organisation_export(client, org, other_org, db):
    _seed(client, org)
    r = client.post("/data/exports", headers=org.h())
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]
    listed = client.get("/data/exports", headers=org.h()).json()
    assert listed[0]["id"] == job_id and listed[0]["status"] == "succeeded"
    result = listed[0]["result"]
    assert result["rows"]["sales_records"] == 60 and result["rows"]["users"] == 4

    dl = client.get(f"/data/exports/{job_id}/download", headers=org.h())
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(dl.content))
    assert {"products.csv", "sales_records.csv", "users.csv", "audit_events.csv", "manifest.json"} <= set(zf.namelist())
    users = list(csv.DictReader(io.StringIO(zf.read("users.csv").decode())))
    assert "password_hash" not in users[0] and "totp_secret" not in users[0]
    hashes = {u.password_hash for u in db.scalars(select(User).where(User.org_id == org.org_id))}
    assert not any(h in zf.read(n).decode() for n in zf.namelist() for h in hashes)
    suppliers = list(csv.DictReader(io.StringIO(zf.read("suppliers.csv").decode())))
    assert suppliers[0]["name"] == "'=Evil()"
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["rows"]["sales_records"] == 60 and manifest["organisation"]["name"] == "Acme"

    assert client.get(f"/data/exports/{job_id}/download", headers=other_org.h()).status_code == 404
    actions = {e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.org_id == org.org_id))}
    assert {"data.export_requested", "data.export_downloaded"} <= actions

    # After 7 days the file is removed and the export can no longer be downloaded.
    job = db.get(Job, __import__("uuid").UUID(job_id))
    key = job.result["key"]
    job.finished_at = datetime.now(UTC) - timedelta(days=8)
    db.commit()
    assert data_rights.expire_exports(db) == 1
    assert not get_storage().exists(key)
    assert client.get(f"/data/exports/{job_id}/download", headers=org.h()).status_code == 409


def test_personal_export(client, org):
    r = client.get("/auth/me/export", headers=org.h("viewer"))
    assert r.status_code == 200
    body = json.loads(r.content)
    assert body["profile"]["email"] == org.emails["viewer"] and "password_hash" not in body["profile"]
    assert any(e["action"] == "auth.login" for e in body["audit_events"])
    assert body["sessions"] and "token_hash" not in body["sessions"][0]


def test_delete_a_user(client, org, db):
    viewer_id = org.users["viewer"]
    h = org.h("viewer")
    assert client.delete(f"/users/{org.users['admin']}", headers=org.h()).status_code == 403  # not yourself
    assert client.delete(f"/users/{viewer_id}", headers=org.h()).status_code == 204
    user = db.get(User, viewer_id)
    db.refresh(user)
    assert user.email.endswith("@deleted.invalid") and user.full_name == "" and not user.is_active
    assert client.get("/auth/me", headers=h).status_code == 401
    r = client.post("/auth/login", json={"email": org.emails["viewer"], "password": PASSWORD})
    assert r.status_code == 401
    assert client.delete(f"/users/{viewer_id}", headers=org.h()).status_code == 409
    # A deleted account cannot be reactivated or given a new role.
    assert client.patch(f"/users/{viewer_id}", json={"is_active": True}, headers=org.h()).status_code == 409
    ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "user.deleted")).one()
    assert ev.details["email"] == org.emails["viewer"]


def test_last_admin_cannot_be_deleted(client, org, db):
    second = User(org_id=org.org_id, email="admin2@example.com", password_hash="x", role="admin")
    db.add(second)
    db.commit()
    # Another active admin (the fixture's) remains, so admin2 can go.
    assert client.delete(f"/users/{second.id}", headers=org.h()).status_code == 204
    # With the fixture admin inactive, admin3 is the only active admin left and must stay.
    third = User(org_id=org.org_id, email="admin3@example.com", password_hash="x", role="admin")
    db.add(third)
    db.execute(text("UPDATE users SET is_active = false WHERE id = :i"), {"i": org.users["admin"]})
    db.commit()
    with pytest.raises(Conflict, match="at least one other active administrator"):
        data_rights.erase_user(db, db.get(User, org.users["analyst"]), third.id)
    db.rollback()
    assert db.get(User, third.id).is_active


def test_organisation_deletion(client, org, other_org, db):
    _seed(client, org)
    _seed(client, other_org)
    client.post("/data/exports", headers=org.h())
    h = org.h()
    bad = client.post("/organisation/deletion", json={"confirm_name": "acme", "current_password": PASSWORD}, headers=h)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "confirmation_mismatch"
    bad = client.post("/organisation/deletion", json={"confirm_name": "Acme", "current_password": "wrong"}, headers=h)
    assert bad.status_code == 422
    r = client.post("/organisation/deletion", json={"confirm_name": "Acme", "current_password": PASSWORD}, headers=h)
    assert r.status_code == 200 and r.json()["scheduled"]["due_at"]
    assert (
        client.get("/auth/me", headers=org.h("viewer")).json()["org_deletion_due_at"] == r.json()["scheduled"]["due_at"]
    )
    assert client.delete("/organisation/deletion", headers=h).status_code == 204
    assert client.get("/organisation/deletion", headers=h).json() == {"scheduled": None}
    client.post("/organisation/deletion", json={"confirm_name": "Acme", "current_password": PASSWORD}, headers=h)

    keys = list(db.scalars(select(Dataset.storage_key).where(Dataset.org_id == org.org_id)))
    assert keys and all(get_storage().exists(k) for k in keys)
    assert data_rights.purge_due_organisations(db) == []  # not due yet
    done = data_rights.purge_due_organisations(db, now=datetime.now(UTC) + timedelta(days=8))
    assert done == [str(org.org_id)]
    db.expire_all()
    assert db.get(Organization, org.org_id) is None
    assert not any(get_storage().exists(k) for k in keys)
    for table in inspect(db.get_bind()).get_table_names():
        cols = {c["name"] for c in inspect(db.get_bind()).get_columns(table)}
        if "org_id" in cols:
            n = db.execute(text(f'SELECT count(*) FROM "{table}" WHERE org_id = :o'), {"o": org.org_id}).scalar()  # noqa: S608
            assert n == 0, f"{table} still has rows of the deleted organisation"
    assert db.scalar(select(func.count()).select_from(Product).where(Product.org_id == other_org.org_id)) == 2
    note = db.scalars(select(AuditEvent).where(AuditEvent.action == "organisation.deleted")).one()
    assert note.org_id is None and note.resource_id == str(org.org_id)
