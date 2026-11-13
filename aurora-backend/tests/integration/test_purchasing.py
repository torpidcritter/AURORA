"""Suppliers and purchase orders: lifecycle, stock movement, concurrency, tenant isolation, suggestions and export."""

from __future__ import annotations

import csv
import io
import threading
from datetime import date, timedelta

from sqlalchemy import select

from app.db.models import AuditEvent, Product, SalesRecord
from app.services import inventory_service


def _product(db, org_id, sku, **kw):
    p = Product(org_id=org_id, sku=sku, name=f"Name {sku}", **kw)
    db.add(p)
    db.flush()
    return p


def _supplier(client, org, name="Acme Supply", **kw):
    r = client.post("/suppliers", json={"name": name, **kw}, headers=org.h("operator"))
    assert r.status_code == 201, r.text
    return r.json()


def _order(client, org, supplier_id, lines, role="operator"):
    r = client.post("/purchase-orders", json={"supplier_id": supplier_id, "lines": lines}, headers=org.h(role))
    assert r.status_code == 201, r.text
    return r.json()


def _act(client, org, po, action, role="operator", **body):
    return client.post(
        f"/purchase-orders/{po['id']}/{action}", json={"expected_version": po["version"], **body}, headers=org.h(role)
    )


def _stock(db, pid):
    db.expire_all()
    p = db.get(Product, pid)
    return p.on_hand, p.on_order


# --- suppliers --------------------------------------------------------------------------------------------------------


def test_supplier_crud_unique_name_and_version_guard(client, org):
    s = _supplier(client, org, email="orders@acme.example", lead_time_days=12, min_order_value=500)
    assert s["version"] == 1 and s["product_count"] == 0
    dup = client.post("/suppliers", json={"name": "  acme supply "}, headers=org.h("operator"))
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "supplier_exists"

    r = client.patch(
        f"/suppliers/{s['id']}", json={"expected_version": 1, "lead_time_days": 9}, headers=org.h("operator")
    )
    assert r.status_code == 200 and r.json()["lead_time_days"] == 9 and r.json()["version"] == 2
    stale = client.patch(f"/suppliers/{s['id']}", json={"expected_version": 1, "notes": "x"}, headers=org.h("admin"))
    assert stale.status_code == 409
    blank = client.patch(f"/suppliers/{s['id']}", json={"expected_version": 2, "name": " "}, headers=org.h("admin"))
    assert blank.status_code == 422
    bad = client.post("/suppliers", json={"name": "B", "email": "not-an-email"}, headers=org.h("admin"))
    assert bad.status_code == 422


def test_supplier_lead_time_is_used_when_the_product_has_none(client, org, db):
    s = _supplier(client, org, lead_time_days=21)
    p = _product(db, org.org_id, "A", on_hand=5, inventory_known=True)
    own = _product(db, org.org_id, "B", on_hand=5, inventory_known=True, lead_time_days=3)
    db.commit()
    r = client.post(
        "/suppliers/assign", json={"supplier_id": s["id"], "product_ids": [str(p.id), str(own.id)]}, headers=org.h()
    )
    assert r.json() == {"changed": 2}
    db.expire_all()
    params, notes = inventory_service.resolve_params(db.get(Product, p.id), None, None, 21)
    assert params["lead_time_days"] == 21 and any("supplier" in n.lower() for n in notes)
    params, _ = inventory_service.resolve_params(db.get(Product, own.id), None, None, 21)
    assert params["lead_time_days"] == 3
    assert client.get(f"/suppliers/{s['id']}", headers=org.h("viewer")).json()["product_count"] == 2
    assert client.get(f"/products/{p.id}", headers=org.h("viewer")).json()["supplier_id"] == s["id"]


# --- lifecycle --------------------------------------------------------------------------------------------------------


def test_full_lifecycle_moves_stock_and_is_audited(client, org, db):
    s = _supplier(client, org, lead_time_days=10)
    a = _product(db, org.org_id, "A", on_hand=4, inventory_known=True, unit_cost=2.5)
    b = _product(db, org.org_id, "B", on_hand=0, inventory_known=True)
    db.commit()
    po = _order(
        client, org, s["id"], [{"product_id": str(a.id), "quantity": 10}, {"product_id": str(b.id), "quantity": 6}]
    )
    assert po["number"] == "PO-000001" and po["status"] == "draft"
    line_a = next(ln for ln in po["lines"] if ln["sku"] == "A")
    assert line_a["unit_cost"] == 2.5 and line_a["line_value"] == 25.0  # defaulted from the product
    assert po["total_complete"] is False  # B has no cost
    assert _stock(db, a.id) == (4, 0)

    # Operators cannot approve; analysts can.
    assert _act(client, org, po, "approve", role="operator").status_code == 403
    assert _act(client, org, po, "send").status_code == 409  # must be approved first
    po = _act(client, org, po, "approve", role="analyst").json()
    assert po["status"] == "approved" and po["approved_by"] == str(org.users["analyst"])
    edit = client.patch(
        f"/purchase-orders/{po['id']}", json={"expected_version": po["version"], "notes": "x"}, headers=org.h()
    )
    assert edit.status_code == 409 and edit.json()["error"]["code"] == "not_draft"

    po = _act(client, org, po, "send").json()
    assert po["status"] == "sent" and po["supplier_name"] == "Acme Supply"
    assert po["expected_date"] == (date.today() + timedelta(days=10)).isoformat()
    assert _stock(db, a.id) == (4, 10) and _stock(db, b.id) == (0, 6)

    part = _act(client, org, po, "receive", lines=[{"product_id": str(a.id), "quantity": 4}])
    assert part.status_code == 200, part.text
    po = part.json()
    assert po["status"] == "partially_received"
    assert _stock(db, a.id) == (8, 6)
    over = _act(client, org, po, "receive", lines=[{"product_id": str(a.id), "quantity": 7}])
    assert over.status_code == 422  # only 6 outstanding

    po = _act(client, org, po, "receive").json()  # the rest
    assert po["status"] == "received" and po["received_at"]
    assert _stock(db, a.id) == (14, 0) and _stock(db, b.id) == (6, 0)
    assert _act(client, org, po, "cancel").status_code == 409

    actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.org_id == org.org_id)))
    assert {
        "purchase_order.created",
        "purchase_order.approved",
        "purchase_order.sent",
        "purchase_order.received",
    } <= actions


def test_cancelling_a_sent_order_releases_only_what_is_outstanding(client, org, db):
    s = _supplier(client, org)
    a = _product(db, org.org_id, "A", on_hand=0, on_order=3, inventory_known=True)  # 3 on order from elsewhere
    db.commit()
    po = _order(client, org, s["id"], [{"product_id": str(a.id), "quantity": 10}])
    po = _act(client, org, po, "approve", role="analyst").json()
    po = _act(client, org, po, "send").json()
    po = _act(client, org, po, "receive", lines=[{"product_id": str(a.id), "quantity": 4}]).json()
    assert _stock(db, a.id) == (4, 9)
    po = _act(client, org, po, "cancel").json()
    assert po["status"] == "cancelled"
    assert _stock(db, a.id) == (4, 3)


def test_drafts_edit_delete_and_validation(client, org, db):
    s = _supplier(client, org)
    a = _product(db, org.org_id, "A", pack_size=12, min_order_qty=24)
    b = _product(db, org.org_id, "B")
    db.commit()
    dup = client.post(
        "/purchase-orders",
        json={"lines": [{"product_id": str(a.id), "quantity": 1}, {"product_id": str(a.id), "quantity": 2}]},
        headers=org.h("operator"),
    )
    assert dup.status_code == 422
    po = _order(client, org, None, [{"product_id": str(a.id), "quantity": 13, "unit_cost": None}])
    assert any("pack size" in w for w in po["warnings"]) and any("minimum order" in w for w in po["warnings"])
    assert _act(client, org, po, "approve", role="analyst").status_code == 422  # no supplier yet

    r = client.patch(
        f"/purchase-orders/{po['id']}",
        json={
            "expected_version": po["version"],
            "supplier_id": s["id"],
            "lines": [{"product_id": str(b.id), "quantity": 5, "unit_cost": 1.0}],
        },
        headers=org.h("operator"),
    )
    assert r.status_code == 200, r.text
    po = r.json()
    assert [ln["sku"] for ln in po["lines"]] == ["B"] and po["total_value"] == 5.0 and po["warnings"] == []
    stale = client.delete(f"/purchase-orders/{po['id']}?expected_version=1", headers=org.h("operator"))
    assert stale.status_code == 409
    gone = client.delete(f"/purchase-orders/{po['id']}?expected_version={po['version']}", headers=org.h("operator"))
    assert gone.status_code == 204
    assert client.get(f"/purchase-orders/{po['id']}", headers=org.h("viewer")).status_code == 404


def test_inactive_supplier_and_min_order_value_warning(client, org, db):
    s = _supplier(client, org, min_order_value=100)
    a = _product(db, org.org_id, "A", unit_cost=2.0)
    db.commit()
    po = _order(client, org, s["id"], [{"product_id": str(a.id), "quantity": 10}])
    assert any("below" in w and "minimum" in w for w in po["warnings"])
    client.patch(f"/suppliers/{s['id']}", json={"expected_version": 1, "is_active": False}, headers=org.h())
    r = client.post("/purchase-orders", json={"supplier_id": s["id"], "lines": []}, headers=org.h("operator"))
    assert r.status_code == 422
    assert client.get("/suppliers", headers=org.h()).json() == []
    assert len(client.get("/suppliers?include_inactive=true", headers=org.h()).json()) == 1


def test_list_filters_and_numbers_are_sequential(client, org, db):
    s = _supplier(client, org)
    a = _product(db, org.org_id, "A", unit_cost=1.0)
    db.commit()
    first = _order(client, org, s["id"], [{"product_id": str(a.id), "quantity": 2}])
    second = _order(client, org, None, [])
    assert (first["number"], second["number"]) == ("PO-000001", "PO-000002")
    _act(client, org, first, "approve", role="analyst")
    page = client.get("/purchase-orders?status=approved", headers=org.h("viewer")).json()
    assert page["total"] == 1 and page["items"][0]["total_value"] == 2.0
    assert client.get("/purchase-orders?status=open", headers=org.h("viewer")).json()["total"] == 2
    assert client.get("/purchase-orders?status=bogus", headers=org.h("viewer")).status_code == 422


# --- concurrency ------------------------------------------------------------------------------------------------------


def test_concurrent_sends_add_stock_on_order_once(client, org, db):
    s = _supplier(client, org)
    a = _product(db, org.org_id, "A", inventory_known=True)
    db.commit()
    po = _order(client, org, s["id"], [{"product_id": str(a.id), "quantity": 10}])
    po = _act(client, org, po, "approve", role="analyst").json()
    org.token("operator")  # log in before the threads start
    n = 5
    barrier = threading.Barrier(n)
    codes: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        r = client.post(
            f"/purchase-orders/{po['id']}/send", json={"expected_version": po["version"]}, headers=org.h("operator")
        )
        with lock:
            codes.append(r.status_code)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200] + [409] * (n - 1)
    assert _stock(db, a.id)[1] == 10


def test_concurrent_creates_get_distinct_numbers(client, org):
    org.token("operator")
    n = 5
    barrier = threading.Barrier(n)
    numbers: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        r = client.post("/purchase-orders", json={"lines": []}, headers=org.h("operator"))
        with lock:
            numbers.append(r.json().get("number", r.text))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(numbers) == [f"PO-00000{i}" for i in range(1, n + 1)]


# --- isolation --------------------------------------------------------------------------------------------------------


def test_other_tenants_cannot_see_or_use_suppliers_and_orders(client, org, other_org, db):
    s = _supplier(client, org)
    a = _product(db, org.org_id, "A")
    theirs = _product(db, other_org.org_id, "T")
    db.commit()
    po = _order(client, org, s["id"], [{"product_id": str(a.id), "quantity": 1}])
    h = other_org.h()
    assert client.get(f"/suppliers/{s['id']}", headers=h).status_code == 404
    assert client.get("/suppliers", headers=h).json() == []
    assert client.get(f"/purchase-orders/{po['id']}", headers=h).status_code == 404
    assert client.get(f"/purchase-orders/{po['id']}/export.csv", headers=h).status_code == 404
    assert client.get("/purchase-orders", headers=h).json()["total"] == 0
    assert (
        client.post(f"/purchase-orders/{po['id']}/approve", json={"expected_version": 1}, headers=h).status_code == 404
    )
    # Their supplier and product ids are unknown here, and ours are unknown to them.
    r = client.post("/purchase-orders", json={"supplier_id": s["id"], "lines": []}, headers=h)
    assert r.status_code == 404
    r = client.post("/purchase-orders", json={"lines": [{"product_id": str(a.id), "quantity": 1}]}, headers=h)
    assert r.status_code == 404
    r = client.post("/suppliers/assign", json={"supplier_id": s["id"], "product_ids": [str(theirs.id)]}, headers=h)
    assert r.status_code == 404
    r = client.post("/suppliers/assign", json={"supplier_id": None, "product_ids": [str(a.id)]}, headers=h)
    assert r.status_code == 404


# --- suggestions ------------------------------------------------------------------------------------------------------


def _history(db, org_id, pid, per_day=10.0, days=120):
    start = date(2026, 1, 1)
    for i in range(days):
        db.add(SalesRecord(org_id=org_id, product_id=pid, sale_date=start + timedelta(days=i), quantity=per_day))


def test_suggest_drafts_one_order_per_supplier_and_skips_busy_products(client, org, db):
    s1 = _supplier(client, org, name="One")
    s2 = _supplier(client, org, name="Two")
    low = [_product(db, org.org_id, f"L{i}", on_hand=1, inventory_known=True, unit_cost=1.0) for i in range(4)]
    plenty = _product(db, org.org_id, "P", on_hand=100_000, inventory_known=True)
    unknown = _product(db, org.org_id, "U")  # no inventory figure
    for p in [*low, plenty, unknown]:
        _history(db, org.org_id, p.id)
    db.commit()
    client.post(
        "/suppliers/assign",
        json={"supplier_id": s1["id"], "product_ids": [str(low[0].id), str(low[1].id)]},
        headers=org.h(),
    )
    client.post("/suppliers/assign", json={"supplier_id": s2["id"], "product_ids": [str(low[2].id)]}, headers=org.h())
    # low[1] is already on a draft, so it is skipped.
    _order(client, org, s1["id"], [{"product_id": str(low[1].id), "quantity": 1}])

    r = client.post("/purchase-orders/suggest", json={}, headers=org.h("operator"))
    assert r.status_code == 201, r.text
    out = r.json()
    by_supplier = {o["supplier_name"]: o for o in out["created"]}
    assert set(by_supplier) == {"One", "Two", None}
    assert [ln["sku"] for ln in by_supplier["One"]["lines"]] == ["L0"]
    assert [ln["sku"] for ln in by_supplier["Two"]["lines"]] == ["L2"]
    assert [ln["sku"] for ln in by_supplier[None]["lines"]] == ["L3"]
    assert all(o["status"] == "draft" and o["source"] == "suggested" for o in out["created"])
    line = by_supplier["One"]["lines"][0]
    assert line["quantity"] == line["suggested_qty"] > 0 and line["suggestion"]["risk_level"]
    assert out["skipped"] == {"already_on_order": 1, "no_reorder_needed": 1, "no_data": 1}

    # Running it again creates nothing: everything that needs ordering is now on a draft.
    again = client.post("/purchase-orders/suggest", json={}, headers=org.h("operator")).json()
    assert again["created"] == [] and again["skipped"]["already_on_order"] == 4


# --- export -----------------------------------------------------------------------------------------------------------


def test_csv_export_neutralises_formulas(client, org, db):
    s = _supplier(client, org, name='=HYPERLINK("http://x")')
    a = _product(db, org.org_id, "+SUM(A1)", unit_cost=1.5)
    a.name = "@cmd"
    db.commit()
    po = _order(client, org, s["id"], [{"product_id": str(a.id), "quantity": 2}])
    r = client.get(f"/purchase-orders/{po['id']}/export.csv", headers=org.h("viewer"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="PO-000001.csv"' in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[1] == ["supplier", '\'=HYPERLINK("http://x")']
    line = rows[6]
    assert line[:3] == ["'+SUM(A1)", "'@cmd", "2"] and line[4] == "3.0"
