from __future__ import annotations

import io
import threading

from app.devtools.synthetic import generate_sales, to_csv_bytes


def upload(client, h, data: bytes, name="sales.csv", **form):
    # Tagged uploads use the Analyst+ programmatic endpoint; plain uploads the Operator+ one (spec 8.2).
    path = "/datasets/upload" if "tags" in form else "/upload-dataset"
    return client.post(path, files={"file": (name, io.BytesIO(data), "text/csv")}, data=form, headers=h)


def test_upload_review_and_products(client, org):
    df = generate_sales(n_products=3, days=60)
    # Operators may upload but not attach metadata tags.
    assert upload(client, org.h("operator"), to_csv_bytes(df), tags='{"source": "pos"}').status_code == 403
    r = upload(client, org.h("analyst"), to_csv_bytes(df), tags='{"source": "pos"}')
    assert r.status_code == 201, r.text
    ds = r.json()["dataset"]
    assert ds["status"] == "imported"
    assert ds["accepted_rows"] == 180 and ds["rejected_rows"] == 0
    assert ds["quality_score"] == 100.0
    assert ds["tags"] == {"source": "pos"}
    assert ds["column_mapping"]["sales"] == "units_sold"

    # same file again is idempotent
    r2 = upload(client, org.h("operator"), to_csv_bytes(df))
    assert r2.json()["duplicate"] is True and r2.json()["dataset"]["id"] == ds["id"]

    lst = client.get("/datasets", headers=org.h("viewer")).json()
    assert lst["total"] == 1

    prods = client.get("/products?sort=units_30d&order=desc", headers=org.h("viewer")).json()
    assert prods["total"] == 3
    units = [p["units_30d"] for p in prods["items"]]
    assert units == sorted(units, reverse=True)
    pid = prods["items"][0]["id"]
    hist = client.get(f"/products/{pid}/history?days=30", headers=org.h("viewer")).json()
    assert len(hist["points"]) == 30
    assert client.get("/products?q=SKU-002", headers=org.h("viewer")).json()["total"] == 1
    assert client.get("/products/categories", headers=org.h("viewer")).json()


def test_reupload_overlapping_dates_replaces_values(client, org):
    upload(client, org.h("operator"), b"date,sku,qty\n2025-01-01,A,5\n2025-01-02,A,6\n")
    upload(client, org.h("operator"), b"date,sku,qty\n2025-01-02,A,9\n2025-01-03,A,1\n")
    pid = client.get("/products", headers=org.h()).json()["items"][0]["id"]
    pts = client.get(f"/products/{pid}/history", headers=org.h()).json()["points"]
    assert [p["units"] for p in pts] == [5, 9, 1]


def test_rejected_and_malformed_uploads(client, org):
    r = upload(client, org.h("operator"), b"foo,bar\n1,2\n")
    assert r.status_code == 201
    assert r.json()["dataset"]["status"] == "rejected"
    assert "Required columns" in r.json()["dataset"]["quality_report"]["rejection_reason"]
    assert upload(client, org.h("operator"), b"", name="x.csv").status_code == 422
    assert upload(client, org.h("operator"), b"MZ\x90\x00", name="evil.exe").status_code == 422
    assert upload(client, org.h("operator"), b"date,sku,qty", name="x.xlsx").status_code == 422
    assert upload(client, org.h("analyst"), b"date,sku,qty\n", tags="not-json").status_code == 422


def test_gap_policy_interpolate(client, org):
    upload(client, org.h("operator"), b"date,sku,qty\n2025-01-01,A,2\n2025-01-04,A,8\n", missing_policy="interpolate")
    pid = client.get("/products", headers=org.h()).json()["items"][0]["id"]
    pts = client.get(f"/products/{pid}/history", headers=org.h()).json()["points"]
    assert [p["units"] for p in pts] == [2, 4, 6, 8]
    assert [p["imputed"] for p in pts] == [False, True, True, False]


def test_upload_permissions(client, org):
    assert upload(client, org.h("viewer"), b"date,sku,qty\n2025-01-01,A,1\n").status_code == 403
    assert upload(client, {}, b"date,sku,qty\n2025-01-01,A,1\n").status_code == 401


def test_upload_size_limit(client, org, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_bytes", 100)
    r = upload(client, org.h("operator"), b"date,sku,qty\n" + b"2025-01-01,A,1\n" * 20)
    assert r.status_code == 422


def test_tenant_isolation_for_data(client, org, other_org):
    upload(client, org.h("operator"), b"date,sku,qty\n2025-01-01,A,1\n")
    pid = client.get("/products", headers=org.h()).json()["items"][0]["id"]
    ds_id = client.get("/datasets", headers=org.h()).json()["items"][0]["id"]
    assert client.get("/products", headers=other_org.h()).json()["total"] == 0
    assert client.get(f"/products/{pid}", headers=other_org.h()).status_code == 404
    assert client.get(f"/products/{pid}/history", headers=other_org.h()).status_code == 404
    assert client.get(f"/datasets/{ds_id}", headers=other_org.h()).status_code == 404
    r = client.patch(f"/products/{pid}/inventory", json={"expected_version": 1, "on_hand": 5}, headers=other_org.h())
    assert r.status_code == 404
    # same SKU in another org is a different product
    upload(client, other_org.h("operator"), b"date,sku,qty\n2025-01-01,A,7\n")
    assert client.get("/products", headers=other_org.h()).json()["total"] == 1


def test_inventory_update_optimistic_concurrency(client, org):
    upload(client, org.h("operator"), b"date,sku,qty\n2025-01-01,A,1\n")
    prod = client.get("/products", headers=org.h()).json()["items"][0]
    assert prod["inventory_known"] is False
    r = client.patch(
        f"/products/{prod['id']}/inventory",
        json={"expected_version": prod["version"], "on_hand": 40, "lead_time_days": 5},
        headers=org.h("operator"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["on_hand"] == 40 and r.json()["version"] == prod["version"] + 1 and r.json()["inventory_known"]
    stale = client.patch(
        f"/products/{prod['id']}/inventory",
        json={"expected_version": prod["version"], "on_hand": 10},
        headers=org.h("operator"),
    )
    assert stale.status_code == 409
    bad = client.patch(
        f"/products/{prod['id']}/inventory", json={"expected_version": 2, "on_hand": -1}, headers=org.h()
    )
    assert bad.status_code == 422
    assert (
        client.patch(
            f"/products/{prod['id']}/inventory", json={"expected_version": 2, "on_hand": 1}, headers=org.h("viewer")
        ).status_code
        == 403
    )


def test_concurrent_inventory_updates_exactly_one_wins(app, org):
    from fastapi.testclient import TestClient

    c = TestClient(app)
    upload(c, org.h("operator"), b"date,sku,qty\n2025-01-01,A,1\n")
    prod = c.get("/products", headers=org.h()).json()["items"][0]
    h = org.h("operator")
    results: list[int] = []

    def worker(n: int) -> None:
        r = TestClient(app).patch(
            f"/products/{prod['id']}/inventory", json={"expected_version": prod["version"], "on_hand": n}, headers=h
        )
        results.append(r.status_code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results).count(200) == 1
    assert results.count(409) == 7


def test_inventory_snapshot_upload(client, org):
    upload(client, org.h("operator"), b"date,sku,qty\n2025-01-01,A,1\n2025-01-01,B,1\n")
    csv = b"sku,on_hand,lead_time_days\nA,25,4\nB,-3,4\nZ,1,1\n"
    r = client.post(
        "/inventory/upload", files={"file": ("inv.csv", io.BytesIO(csv), "text/csv")}, headers=org.h("operator")
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == 1 and body["unknown_count"] == 1 and len(body["errors"]) == 1
