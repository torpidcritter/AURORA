"""Audit log export and per-organisation retention (ADR-039)."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import AuditEvent
from app.services import audit, audit_export, maintenance


def _event(db, org_id, action, *, days_ago=0, actor="a@example.com", details=None, outcome="success"):
    ev = audit.record(db, action, org_id=org_id, actor_email=actor, details=details or {}, outcome=outcome)
    db.flush()
    ev.created_at = datetime.now(UTC) - timedelta(days=days_ago)
    return ev


def test_export_csv_and_jsonl_with_filters(client, org, other_org, db):
    _event(db, org.org_id, "product.update", days_ago=3, details={"sku": "=HYPERLINK(1)"})
    _event(db, org.org_id, "auth.login", days_ago=2, actor="=cmd@example.com", outcome="failure")
    _event(db, org.org_id, "product.delete", days_ago=1)
    _event(db, other_org.org_id, "product.update", days_ago=1)
    db.commit()

    r = client.get("/audit/export?action=product.", headers=org.h("analyst"))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="aurora-audit-' in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [x["action"] for x in rows] == ["product.update", "product.delete"]  # oldest first, own org only
    assert json.loads(rows[0]["details"]) == {"sku": "=HYPERLINK(1)"}  # JSON text starts with "{", not a formula

    r = client.get("/audit/export?format=csv&outcome=failure", headers=org.h())
    (row,) = list(csv.DictReader(io.StringIO(r.text)))
    assert row["actor_email"] == "'=cmd@example.com"  # spreadsheet formulas neutralised

    since = (datetime.now(UTC) - timedelta(days=2, hours=12)).isoformat()
    r = client.get("/audit/export", params={"format": "jsonl", "since": since}, headers=org.h())
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(x) for x in r.text.splitlines()]
    assert [x["action"] for x in lines][:2] == ["auth.login", "product.delete"]

    # The export itself is audited, with its filters and size.
    exports = db.scalars(
        select(AuditEvent).where(AuditEvent.org_id == org.org_id, AuditEvent.action == "audit.export")
    ).all()
    assert len(exports) == 3
    assert exports[0].details["rows"] == 2 and exports[0].details["action"] == "product."

    bad = client.get("/audit/export", params={"since": since, "until": since}, headers=org.h())
    assert bad.status_code == 422
    assert client.get("/audit/export?format=xml", headers=org.h()).status_code == 422


def test_export_is_capped(client, org, db, monkeypatch):
    for i in range(3):
        _event(db, org.org_id, "x.y", days_ago=i)
    db.commit()
    monkeypatch.setattr(audit_export, "MAX_EXPORT_ROWS", 2)
    r = client.get("/audit/export", headers=org.h())
    assert r.status_code == 422 and "Narrow the date range" in r.json()["error"]["message"]


def test_list_supports_until(client, org, db):
    _event(db, org.org_id, "old.one", days_ago=10)
    _event(db, org.org_id, "new.one", days_ago=0)
    db.commit()
    until = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    got = client.get("/audit", params={"until": until}, headers=org.h()).json()
    assert [e["action"] for e in got["items"]] == ["old.one"]


def test_retention_per_organisation(client, org, other_org, db):
    r = client.patch("/settings/org", json={"audit_retention_days": 30}, headers=org.h("admin"))
    assert r.status_code == 200, r.text
    assert client.patch("/settings/org", json={"audit_retention_days": 7}, headers=org.h("admin")).status_code == 422
    _event(db, org.org_id, "keep.me", days_ago=20)
    _event(db, org.org_id, "drop.me", days_ago=40)
    _event(db, org.org_id, "drop.me.too", days_ago=400)
    _event(db, other_org.org_id, "other.keep", days_ago=40)  # default retention (365 days)
    _event(db, other_org.org_id, "other.drop", days_ago=400)
    _event(db, None, "system.old", days_ago=400)
    db.commit()

    counts = maintenance.purge_audit(db)
    assert counts[str(org.org_id)] == 2 and counts[str(other_org.org_id)] == 1 and counts["no_organisation"] == 1
    left = {e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.org_id == org.org_id))}
    assert {"keep.me", "audit.retention_purge"} <= left and not {"drop.me", "drop.me.too"} & left
    note = db.scalars(
        select(AuditEvent).where(AuditEvent.org_id == org.org_id, AuditEvent.action == "audit.retention_purge")
    ).one()
    assert note.details["deleted"] == 2 and note.details["retention_days"] == 30
    other = {e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.org_id == other_org.org_id))}
    assert "other.keep" in other and "other.drop" not in other

    # A second run has nothing left to delete and adds no further notes.
    assert str(org.org_id) not in maintenance.purge_audit(db)
    notes = db.scalars(
        select(AuditEvent).where(AuditEvent.org_id == org.org_id, AuditEvent.action == "audit.retention_purge")
    ).all()
    assert len(notes) == 1
