"""Scheduled imports (ADR-050): file URLs and WooCommerce, against fake servers. No real store or file host is used."""

from __future__ import annotations

import base64
import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.core import outbound
from app.core.config import get_settings
from app.db.models import Alert, AuditEvent, Dataset, ImportRun, ImportSource, Product, SalesRecord
from app.services import data_rights, import_service

FILE_URL = "https://exports.example.com/feeds/sales-7f3k9.csv?sig=abc123"
STORE = "https://shop.example.com"
# Obviously fake WooCommerce-style credentials for the fake store below.
WOO_CRED = {"consumer_key": "ck_fake_test_key", "consumer_secret": "cs_fake_test_secret"}


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(outbound, "_resolve", lambda host: ["93.184.215.14"])


class FakeServer:
    """Answers import requests the way a file host or WooCommerce store would, and records what it was sent."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.file: bytes = b""
        self.file_status = 200
        self.headers: dict[str, str] = {"content-type": "text/csv"}
        self.orders: list[dict] = []
        self.total_pages: int | None = None

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if req.url.path.endswith("/wp-json/wc/v3/orders"):
            page = int(req.url.params.get("page", "1"))
            size = int(req.url.params.get("per_page", "100"))
            chunk = self.orders[(page - 1) * size : page * size]
            pages = self.total_pages or max(1, -(-len(self.orders) // size))
            return httpx.Response(200, json=chunk, headers={"X-WP-TotalPages": str(pages)})
        return httpx.Response(self.file_status, content=self.file, headers=self.headers)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def server(monkeypatch) -> FakeServer:
    srv = FakeServer()
    monkeypatch.setattr(import_service, "_client", srv.client)
    return srv


def _csv(rows: list[tuple[date, str, int]]) -> bytes:
    lines = ["date,sku,units_sold,product_name"] + [f"{d.isoformat()},{s},{q},Product {s}" for d, s, q in rows]
    return ("\n".join(lines) + "\n").encode()


def _days_ago(n: int) -> date:
    return datetime.now(UTC).date() - timedelta(days=n)


def _order(oid: int, day: date, lines: list[tuple[str, int]], status: str = "completed") -> dict:
    return {
        "id": oid,
        "status": status,
        "date_created": f"{day.isoformat()}T10:30:00",
        "line_items": [{"sku": s, "quantity": q, "name": f"Woo {s}"} for s, q in lines],
    }


def _create(client, org, **body):
    payload = {"name": "Nightly POS export", "kind": "file_url", "url": FILE_URL, **body}
    return client.post("/imports", json=payload, headers=org.h("admin"))


def _sales(db, org, sku: str, source: str | None = None) -> dict[date, float]:
    q = (
        select(SalesRecord.sale_date, SalesRecord.quantity)
        .join(Product, Product.id == SalesRecord.product_id)
        .where(Product.org_id == org.org_id, Product.sku == sku)
    )
    if source:
        q = q.where(SalesRecord.source == source)
    return {d: q for d, q in db.execute(q).all()}


def test_next_run_slots():
    t = datetime(2026, 3, 2, 10, 15, tzinfo=UTC)
    assert import_service.next_run("hourly", 5, t) == datetime(2026, 3, 2, 11, tzinfo=UTC)
    assert import_service.next_run("daily", 5, t) == datetime(2026, 3, 3, 5, tzinfo=UTC)
    assert import_service.next_run("daily", 11, t) == datetime(2026, 3, 2, 11, tzinfo=UTC)
    assert import_service.next_run("daily", 10, datetime(2026, 3, 2, 10, tzinfo=UTC)) == datetime(
        2026, 3, 3, 10, tzinfo=UTC
    )


def test_file_import_runs_through_the_upload_pipeline(client, org, db, server):
    r = _create(client, org, credential={"authorization": "Bearer feed-token-123"}, options={"missing_policy": "zero"})
    assert r.status_code == 201, r.text
    src = r.json()
    # The address's path and query (often the secret) and the credential are never returned.
    assert src["url_hint"] == "https://exports.example.com/…"
    assert src["has_credential"] is True
    assert "sig=" not in r.text and "feed-token" not in r.text
    row = db.get(ImportSource, src["id"])
    assert FILE_URL not in row.url_encrypted and "feed-token" not in (row.secret_encrypted or "")

    server.file = _csv([(_days_ago(3), "SKU-A", 5), (_days_ago(2), "SKU-A", 7), (_days_ago(2), "SKU-B", 1)])
    r = client.post(f"/imports/{src['id']}/run", headers=org.h("admin"))
    assert r.status_code == 200, r.text
    run = r.json()["run"]
    assert run["status"] == "imported" and run["rows"] == 3 and run["products"] == 2
    assert server.requests[-1].headers["authorization"] == "Bearer feed-token-123"
    assert str(server.requests[-1].url) == FILE_URL
    ds = db.get(Dataset, run["dataset_id"])
    assert ds.tags["import_source"] == src["id"] and ds.filename.endswith(".csv")
    assert _sales(db, org, "SKU-A") == {_days_ago(3): 5.0, _days_ago(2): 7.0}

    # The same file again changes nothing; a changed file updates the figures.
    assert client.post(f"/imports/{src['id']}/run", headers=org.h("admin")).json()["run"]["status"] == "unchanged"
    server.file = _csv([(_days_ago(2), "SKU-A", 9), (_days_ago(1), "SKU-A", 4)])
    assert client.post(f"/imports/{src['id']}/run", headers=org.h("admin")).json()["run"]["status"] == "imported"
    db.expire_all()
    assert _sales(db, org, "SKU-A") == {_days_ago(3): 5.0, _days_ago(2): 9.0, _days_ago(1): 4.0}

    runs = client.get(f"/imports/{src['id']}/runs", headers=org.h("admin")).json()
    assert [x["status"] for x in runs] == ["imported", "unchanged", "imported"]
    events = db.scalars(select(AuditEvent).where(AuditEvent.action.like("import.%"))).all()
    assert {e.action for e in events} == {"import.created", "import.run"}
    assert not any("sig=" in json.dumps(e.details) or "feed-token" in json.dumps(e.details) for e in events)


def test_addresses_and_settings_are_checked(client, org, monkeypatch):
    assert _create(client, org, url="http://exports.example.com/a.csv").status_code == 422
    assert _create(client, org, url="https://user:pw@exports.example.com/a.csv").status_code == 422
    assert _create(client, org, credential={"authorization": "Bearer x\r\nX-Evil: 1"}).status_code == 422
    woo = {"kind": "woocommerce", "url": STORE}
    assert _create(client, org, **woo).status_code == 422  # no credential
    assert _create(client, org, **woo, credential={"consumer_key": "ck_a", "consumer_secret": "x y"}).status_code == 422
    assert _create(client, org, **{**woo, "url": f"{STORE}/?page=2"}, credential=WOO_CRED).status_code == 422
    assert _create(client, org, **woo, credential=WOO_CRED, options={"lookback_days": 91}).status_code == 422
    for private in ("10.0.0.5", "127.0.0.1", "169.254.169.254"):
        monkeypatch.setattr(outbound, "_resolve", lambda host, ip=private: [ip])
        r = _create(client, org)
        assert r.status_code == 422 and "private" in r.json()["error"]["message"]
    monkeypatch.setattr(outbound, "_resolve", lambda host: ["93.184.215.14"])
    r = _create(client, org, **woo, credential=WOO_CRED)
    assert r.status_code == 201 and r.json()["options"] == {"lookback_days": 14}
    assert r.json()["url_hint"] == "https://shop.example.com/…"


def test_failures_are_explained_and_repeated_ones_pause_the_source(client, org, db, server, monkeypatch):
    src = _create(client, org).json()

    def run_now() -> dict:
        return client.post(f"/imports/{src['id']}/run", headers=org.h("admin")).json()

    server.file_status = 401
    assert "refused access" in run_now()["run"]["message"]
    server.file_status, server.headers = 302, {"location": "https://elsewhere.example.com/x.csv"}
    assert "redirect" in run_now()["run"]["message"]
    server.file_status, server.headers = 200, {"content-type": "text/csv"}
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 50)
    server.file = _csv([(_days_ago(1), "SKU-A", 1)] * 5)
    assert "larger than" in run_now()["run"]["message"]
    monkeypatch.undo()
    monkeypatch.setattr(outbound, "_resolve", lambda host: ["93.184.215.14"])
    monkeypatch.setattr(import_service, "_client", server.client)
    # A file with no usable rows: the run fails, and the rejected dataset is kept so the report can be read.
    server.file = b"date,sku,units_sold\nnot-a-date,SKU-A,abc\n"
    out = run_now()
    assert out["run"]["status"] == "failed" and "rejected" in out["run"]["message"]
    assert db.get(Dataset, out["run"]["dataset_id"]).status == "rejected"
    assert out["source"]["consecutive_failures"] == 4 and out["source"]["is_active"] is True
    # The same rejected file again is still a failure, and the fifth in a row pauses the source with an alert.
    out = run_now()
    assert out["run"]["status"] == "failed" and out["source"]["is_active"] is False
    alert = db.scalar(select(Alert).where(Alert.kind == "import_failed"))
    assert alert is not None and alert.severity == "high" and "Nightly POS export" in alert.title
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "import.paused")) is not None

    # Resuming starts the count again.
    r = client.patch(f"/imports/{src['id']}", json={"expected_version": 1, "is_active": True}, headers=org.h("admin"))
    assert r.status_code == 200 and r.json()["consecutive_failures"] == 0 and r.json()["is_active"]


def test_woocommerce_replaces_its_window_and_nothing_else(client, org, db, server):
    r = _create(client, org, name="Web store", kind="woocommerce", url=f"{STORE}/", credential=WOO_CRED)
    assert r.status_code == 201, r.text
    src = r.json()
    assert "cs_fake" not in r.text
    row = db.get(ImportSource, src["id"])
    tag = import_service.sales_source(row)

    # A sale of the same SKU from a manual upload, and one this store imported before the window: both must stay.
    server.orders = [
        _order(1, _days_ago(3), [("SKU-W", 2), ("SKU-X", 1)]),
        _order(2, _days_ago(3), [("SKU-W", 3)], status="processing"),
        _order(3, _days_ago(2), [("SKU-W", 4)], status="cancelled"),
        _order(4, _days_ago(2), [("SKU-W", 1), ("", 5)], status="on-hold"),
        _order(5, _days_ago(1), [("SKU-W", 6)], status="pending"),
    ] + [_order(100 + i, _days_ago(5), [("SKU-Y", 1)]) for i in range(150)]
    out = client.post(f"/imports/{src['id']}/run", headers=org.h("admin")).json()["run"]
    assert out["status"] == "imported", out
    assert "155 orders read" in out["message"] and "1 line(s) without a SKU" in out["message"]
    reqs = [q for q in server.requests if q.url.path.endswith("/orders")]
    assert [q.url.params["page"] for q in reqs] == ["1", "2"]
    expected = base64.b64encode(b"ck_fake_test_key:cs_fake_test_secret").decode()
    assert reqs[0].headers["authorization"] == f"Basic {expected}"
    assert str(reqs[0].url).startswith(f"{STORE}/wp-json/wc/v3/orders?")

    assert _sales(db, org, "SKU-W", tag) == {_days_ago(3): 5.0, _days_ago(2): 1.0}
    assert _sales(db, org, "SKU-Y", tag) == {_days_ago(5): 150.0}
    p = db.scalar(select(Product).where(Product.org_id == org.org_id, Product.sku == "SKU-X"))
    assert p.name == "Woo SKU-X" and p.inventory_known is False

    # An older day from this store (outside the window) and an upload for a window day stay untouched; the store's
    # own window is replaced, so a later cancellation removes that order's units.
    old_day = _days_ago(40)
    db.add(SalesRecord(org_id=org.org_id, product_id=p.id, sale_date=old_day, quantity=9, source=tag))
    db.add(SalesRecord(org_id=org.org_id, product_id=p.id, sale_date=_days_ago(3), quantity=7, source="upload"))
    db.commit()
    server.orders[0]["status"] = "cancelled"
    client.post(f"/imports/{src['id']}/run", headers=org.h("admin"))
    db.expire_all()
    assert _sales(db, org, "SKU-W", tag) == {_days_ago(3): 3.0, _days_ago(2): 1.0}
    assert _sales(db, org, "SKU-X", tag) == {old_day: 9.0}
    assert _sales(db, org, "SKU-X", "upload") == {_days_ago(3): 7.0}

    # Running again with nothing changed gives the same result.
    client.post(f"/imports/{src['id']}/run", headers=org.h("admin"))
    db.expire_all()
    assert _sales(db, org, "SKU-W", tag) == {_days_ago(3): 3.0, _days_ago(2): 1.0}


def test_woocommerce_too_many_orders_changes_nothing(client, org, db, server):
    src = _create(client, org, kind="woocommerce", url=STORE, credential=WOO_CRED).json()
    server.orders = [_order(1, _days_ago(2), [("SKU-W", 2)])]
    client.post(f"/imports/{src['id']}/run", headers=org.h("admin"))
    server.orders = [_order(1, _days_ago(2), [("SKU-W", 50)])]
    server.total_pages = import_service.WOO_MAX_PAGES + 1
    out = client.post(f"/imports/{src['id']}/run", headers=org.h("admin")).json()["run"]
    assert out["status"] == "failed" and "shorten the window" in out["message"]
    db.expire_all()
    tag = import_service.sales_source(db.get(ImportSource, src["id"]))
    assert _sales(db, org, "SKU-W", tag) == {_days_ago(2): 2.0}

    server.total_pages = None
    server.orders = {"message": "not a list"}  # type: ignore[assignment]
    server.handler = lambda req: httpx.Response(200, json={"code": "rest_no_route"})  # type: ignore[method-assign]
    out = client.post(f"/imports/{src['id']}/run", headers=org.h("admin")).json()["run"]
    assert out["status"] == "failed" and "not a list" in out["message"]


def test_scheduler_runs_each_due_source_once(client, org, db, server):
    a = _create(client, org).json()
    b = _create(client, org, name="Paused", url="https://exports.example.com/b.csv").json()
    client.patch(f"/imports/{b['id']}", json={"expected_version": 1, "is_active": False}, headers=org.h("admin"))
    db.execute(ImportSource.__table__.update().values(next_run_at=datetime.now(UTC) - timedelta(minutes=1)))
    db.commit()
    server.file = _csv([(_days_ago(1), "SKU-S", 3)])
    assert import_service.run_due(db, client=server.client()) == {"imported": 1, "unchanged": 0, "failed": 0}
    assert import_service.run_due(db, client=server.client()) == {"imported": 0, "unchanged": 0, "failed": 0}
    db.expire_all()
    s = db.get(ImportSource, a["id"])
    assert s.next_run_at > datetime.now(UTC) and s.last_status == "imported"
    assert db.scalar(select(func.count()).select_from(ImportRun)) == 1
    assert db.scalar(select(ImportRun.trigger)) == "schedule"


def test_edit_delete_and_isolation(client, org, other_org, db, server):
    src = _create(client, org).json()
    sid = src["id"]
    for method, path in (
        ("get", f"/imports/{sid}/runs"),
        ("post", f"/imports/{sid}/run"),
        ("delete", f"/imports/{sid}"),
    ):
        assert getattr(client, method)(path, headers=other_org.h("admin")).status_code == 404
    assert client.get("/imports", headers=other_org.h("admin")).json() == []

    r = client.patch(
        f"/imports/{sid}",
        json={"expected_version": 1, "url": "https://exports.example.com/v2.csv", "frequency": "hourly"},
        headers=org.h("admin"),
    )
    assert r.status_code == 200 and r.json()["frequency"] == "hourly" and r.json()["version"] == 2
    stale = client.patch(f"/imports/{sid}", json={"expected_version": 1, "name": "x"}, headers=org.h("admin"))
    assert stale.status_code == 409
    event = db.scalar(select(AuditEvent).where(AuditEvent.action == "import.updated"))
    assert event.details["address_changed"] is True and "v2.csv" not in json.dumps(event.details)

    server.file = _csv([(_days_ago(1), "SKU-D", 3), (_days_ago(2), "SKU-D", 4)])
    client.post(f"/imports/{sid}/run", headers=org.h("admin"))
    keep = _create(client, org, name="Kept", url="https://exports.example.com/k.csv").json()
    server.file = _csv([(_days_ago(1), "SKU-K", 1)])
    client.post(f"/imports/{keep['id']}/run", headers=org.h("admin"))

    r = client.delete(f"/imports/{sid}?remove_sales=true", headers=org.h("admin"))
    assert r.status_code == 200 and r.json()["sales_rows_removed"] == 2
    r = client.delete(f"/imports/{keep['id']}", headers=org.h("admin"))
    assert r.json()["sales_rows_removed"] == 0
    db.expire_all()
    assert _sales(db, org, "SKU-D") == {}
    assert _sales(db, org, "SKU-K") == {_days_ago(1): 1.0}
    assert client.get("/imports", headers=org.h("admin")).json() == []


def test_export_leaves_out_secrets_and_old_runs_are_purged(client, org, db, server):
    src = _create(client, org, credential={"authorization": "Bearer feed-token-123"}).json()
    server.file = _csv([(_days_ago(1), "SKU-E", 2)])
    client.post(f"/imports/{src['id']}/run", headers=org.h("admin"))
    data, counts = data_rights.build_org_export(db, org.org_id)
    assert counts["import_sources"] == 1 and counts["import_runs"] == 1
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        sources = zf.read("import_sources.csv").decode()
    assert "url_encrypted" not in sources and "secret_encrypted" not in sources and "exports.example.com/…" in sources

    db.execute(ImportRun.__table__.update().values(started_at=datetime.now(UTC) - timedelta(days=91)))
    db.commit()
    assert import_service.purge_old(db) == 1


def test_key_rotation_reencrypts_import_addresses_and_credentials(client, org, db, server, monkeypatch):
    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    from app.core import crypto
    from app.services import maintenance

    s = get_settings()
    k_old, k_new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(s, "token_encryption_keys", SecretStr(k_old))
    src = _create(client, org, credential={"authorization": "Bearer feed-token-123"}).json()
    monkeypatch.setattr(s, "token_encryption_keys", SecretStr(f"{k_new},{k_old}"))
    assert maintenance.reencrypt_secrets(db) == {"rotated": 2, "failed": 0}
    monkeypatch.setattr(s, "token_encryption_keys", SecretStr(k_new))
    db.expire_all()
    row = db.get(ImportSource, src["id"])
    assert crypto.decrypt(row.url_encrypted) == FILE_URL
    assert crypto.decrypt(row.secret_encrypted) == "Bearer feed-token-123"
