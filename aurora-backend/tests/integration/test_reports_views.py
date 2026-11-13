"""PDF documents and saved views (ADR-047)."""

from __future__ import annotations

import zlib

import pytest
from sqlalchemy import select

from app.api.deps import Principal
from app.core.errors import Conflict
from app.db.models import AuditEvent, Product, SavedView
from app.services import pdf_reports, purchasing_service, saved_views
from tests.integration.test_assistant import _upload


def _text(pdf: bytes) -> str:
    """Uncompressed content of a Helvetica (fallback font) PDF, for searching."""
    return pdf.decode("latin-1")


def _order(db, org, *, name="Earl Grey 250g", status="draft"):
    p = Product(org_id=org.org_id, sku="EG-1", name=name, unit_cost=2.5)
    q = Product(org_id=org.org_id, sku="EG-2", name="No price")
    db.add_all([p, q])
    db.flush()
    po = purchasing_service.create_order(
        db,
        org.org_id,
        org.users["operator"],
        supplier_id=None,
        lines=[{"product_id": p.id, "quantity": 40}, {"product_id": q.id, "quantity": 5}],
        notes="Deliver to the back door",
    )
    po.status = status
    db.commit()
    return po


def test_purchase_order_pdf(client, org, other_org, db):
    po = _order(db, org)
    r = client.get(f"/purchase-orders/{po.id}/export.pdf", headers=org.h("viewer"))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF-")
    assert r.headers["content-disposition"] == 'attachment; filename="PO-000001.pdf"'
    assert r.headers["cache-control"] == "no-store"
    assert client.get(f"/purchase-orders/{po.id}/export.pdf", headers=other_org.h()).status_code == 404

    body = _text(pdf_reports.purchase_order_pdf(db, org.org_id, po, compress=False))
    for expected in (
        "Purchase order PO-000001",
        "DRAFT",
        "has not been sent",
        "Earl Grey 250g",
        "100.00",
        "Deliver to",
    ):
        assert expected in body, expected
    assert "the total is incomplete" in body  # the second line has no cost

    po.status = "sent"
    db.commit()
    assert "has not been sent" not in _text(pdf_reports.purchase_order_pdf(db, org.org_id, po, compress=False))


def test_unicode_names(db, org):
    po = _order(db, org, name="Чай 绿茶 ünïcode")
    lossy = _text(pdf_reports.purchase_order_pdf(db, org.org_id, po, compress=False))
    assert "?? ??" in lossy and "appear as '?'" in lossy  # Helvetica fallback says what happened
    font = pdf_reports.unicode_font()
    if font is None:
        pytest.skip("DejaVu Sans is not installed here (the container image installs it)")
    full = pdf_reports.purchase_order_pdf(db, org.org_id, po, font_dir=font)
    assert full.startswith(b"%PDF-") and b"DejaVuSans" in full
    assert b"appear as '?'" not in zlib.decompress(full.split(b"stream\n")[1].split(b"\nendstream")[0])


def test_inventory_report(client, org, db):
    _upload(client, org)
    r = client.get("/reports/inventory.pdf", headers=org.h("viewer"))
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")
    body = _text(pdf_reports.inventory_report_pdf(db, org.org_id, compress=False))
    assert "Inventory report" in body and "Products scored" in body and "How these numbers are made" in body
    assert "Products to reorder" in body


def test_saved_views(client, org, other_org, db):
    h = org.h("operator")
    body = {"page": "products", "name": "Tea at risk", "params": {"category": "Tea", "health": "risk", "q": ""}}
    r = client.post("/saved-views", json=body, headers=h)
    assert r.status_code == 201, r.text
    mine = r.json()
    assert mine["params"] == {"category": "Tea", "health": "risk"} and mine["mine"] and not mine["shared"]
    assert client.post("/saved-views", json=body, headers=h).status_code == 409
    bad = client.post("/saved-views", json={**body, "name": "x", "params": {"password": "x"}}, headers=h)
    assert bad.status_code == 422 and "not a filter" in bad.json()["error"]["message"]
    assert client.post("/saved-views", json={**body, "page": "admin"}, headers=h).status_code == 422
    long = client.post("/saved-views", json={**body, "name": "y", "params": {"q": "x" * 201}}, headers=h)
    assert long.status_code == 422

    shared = client.post(
        "/saved-views",
        json={"page": "alerts", "name": "Critical", "params": {"severity": "critical"}, "shared": True},
        headers=h,
    ).json()
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "saved_view.shared")) is not None
    viewer = client.get("/saved-views?page=alerts", headers=org.h("viewer")).json()
    assert [(v["name"], v["mine"]) for v in viewer] == [("Critical", False)]
    assert client.get("/saved-views?page=products", headers=org.h("viewer")).json() == []  # private
    assert client.get("/saved-views?page=alerts", headers=other_org.h()).json() == []
    assert client.delete(f"/saved-views/{mine['id']}", headers=org.h("viewer")).status_code == 404
    assert client.delete(f"/saved-views/{shared['id']}", headers=org.h("viewer")).status_code == 403
    assert client.delete(f"/saved-views/{shared['id']}", headers=org.h("admin")).status_code == 204
    assert client.delete(f"/saved-views/{mine['id']}", headers=h).status_code == 204
    assert db.scalars(select(SavedView)).all() == []

    p = Principal(user_id=org.users["viewer"], org_id=org.org_id, role="viewer", email="v@example.com", ip=None)
    for i in range(saved_views.MAX_PER_PAGE):
        saved_views.create(db, p, page="inventory", name=f"v{i}", params={"only_reorder": True}, shared=False)
    with pytest.raises(Conflict, match="at most 30"):
        saved_views.create(db, p, page="inventory", name="one more", params={}, shared=False)
