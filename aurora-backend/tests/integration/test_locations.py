"""Stock locations: per-location levels that always add up to the product totals, transfers, and purchasing."""

from __future__ import annotations

import threading

from sqlalchemy import func, select

from app.db.models import Product, StockLevel


def _product(db, org_id, sku="A", **kw):
    p = Product(org_id=org_id, sku=sku, name=sku, **kw)
    db.add(p)
    db.flush()
    return p


def _loc(client, org, code, name=None):
    r = client.post("/locations", json={"code": code, "name": name or code}, headers=org.h("admin"))
    assert r.status_code == 201, r.text
    return r.json()


def _stock(client, org, pid):
    r = client.get(f"/products/{pid}/stock", headers=org.h("viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    return body, {row["code"]: (row["on_hand"], row["on_order"]) for row in body["locations"]}


def _assert_totals_match(db, pid):
    db.expire_all()
    p = db.get(Product, pid)
    sums = db.execute(
        select(func.coalesce(func.sum(StockLevel.on_hand), 0), func.coalesce(func.sum(StockLevel.on_order), 0)).where(
            StockLevel.product_id == pid
        )
    ).one()
    assert (p.on_hand, p.on_order) == tuple(sums)


def _patch(client, org, pid, version, role="operator", **body):
    return client.patch(f"/products/{pid}/inventory", json={"expected_version": version, **body}, headers=org.h(role))


def test_default_location_and_legacy_stock_is_kept(client, org, db):
    p = _product(db, org.org_id, on_hand=40, on_order=5, inventory_known=True)  # stock set before locations
    db.commit()
    locs = client.get("/locations", headers=org.h("viewer")).json()
    assert [(loc["code"], loc["is_default"]) for loc in locs] == [("MAIN", True)]
    body, by_code = _stock(client, org, p.id)
    assert by_code == {"MAIN": (40, 5)} and (body["on_hand"], body["on_order"]) == (40, 5)
    _assert_totals_match(db, p.id)
    assert client.get("/locations", headers=org.h()).json()[0]["on_hand"] == 40


def test_counts_need_a_location_once_there_are_several(client, org, db):
    p = _product(db, org.org_id)
    db.commit()
    # One location: a count without a location goes to the default, as before.
    r = _patch(client, org, p.id, 1, on_hand=10)
    assert r.status_code == 200 and r.json()["on_hand"] == 10
    store = _loc(client, org, "STORE-1", "High Street")
    r = _patch(client, org, p.id, 2, on_hand=3)
    assert r.status_code == 422 and "Choose a location" in r.json()["error"]["message"]
    r = _patch(client, org, p.id, 2, on_hand=3, location_id=store["id"])
    assert r.status_code == 200 and r.json()["on_hand"] == 13 and r.json()["version"] == 3
    # Fields that are not stock counts need no location.
    assert _patch(client, org, p.id, 3, lead_time_days=4).status_code == 200
    _, by_code = _stock(client, org, p.id)
    assert by_code == {"MAIN": (10, 0), "STORE-1": (3, 0)}
    _assert_totals_match(db, p.id)


def test_transfer_moves_stock_and_is_validated(client, org, other_org, db):
    p = _product(db, org.org_id)
    db.commit()
    _patch(client, org, p.id, 1, on_hand=10)
    main = client.get("/locations", headers=org.h()).json()[0]
    store = _loc(client, org, "S1")
    body = {"product_id": str(p.id), "from_location_id": main["id"], "to_location_id": store["id"]}
    too_many = client.post("/stock/transfers", json={**body, "quantity": 11}, headers=org.h("operator"))
    assert too_many.status_code == 422 and too_many.json()["error"]["details"]["available"] == 10
    same = {**body, "to_location_id": main["id"], "quantity": 1}
    assert client.post("/stock/transfers", json=same, headers=org.h("operator")).status_code == 422
    assert client.post("/stock/transfers", json={**body, "quantity": 4}, headers=org.h("viewer")).status_code == 403

    r = client.post("/stock/transfers", json={**body, "quantity": 4, "note": "restock"}, headers=org.h("operator"))
    assert r.status_code == 201, r.text
    assert (r.json()["from_code"], r.json()["to_code"], r.json()["quantity"]) == ("MAIN", "S1", 4)
    stock, by_code = _stock(client, org, p.id)
    assert by_code == {"MAIN": (6, 0), "S1": (4, 0)} and stock["on_hand"] == 10
    _assert_totals_match(db, p.id)
    assert client.get("/stock/transfers", headers=org.h("viewer")).json()["total"] == 1
    assert client.get("/locations", headers=org.h()).json()[1]["on_hand"] == 4

    # Another tenant cannot see or use any of it.
    h = other_org.h()
    assert client.get(f"/products/{p.id}/stock", headers=h).status_code == 404
    assert client.post("/stock/transfers", json={**body, "quantity": 1}, headers=h).status_code == 404
    assert client.get("/stock/transfers", headers=h).json()["total"] == 0
    assert [loc["code"] for loc in client.get("/locations", headers=h).json()] == ["MAIN"]
    assert (
        client.patch(f"/locations/{store['id']}", json={"expected_version": 1, "name": "x"}, headers=h).status_code
        == 404
    )


def test_concurrent_transfers_never_oversell(client, org, db):
    p = _product(db, org.org_id)
    db.commit()
    _patch(client, org, p.id, 1, on_hand=5)
    main = client.get("/locations", headers=org.h()).json()[0]
    store = _loc(client, org, "S1")
    body = {"product_id": str(p.id), "from_location_id": main["id"], "to_location_id": store["id"], "quantity": 2}
    org.token("operator")
    n = 5
    barrier = threading.Barrier(n)
    codes: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        r = client.post("/stock/transfers", json=body, headers=org.h("operator"))
        with lock:
            codes.append(r.status_code)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [201, 201, 422, 422, 422]
    _, by_code = _stock(client, org, p.id)
    assert by_code == {"MAIN": (1, 0), "S1": (4, 0)}
    _assert_totals_match(db, p.id)


def test_location_rules(client, org, db):
    p = _product(db, org.org_id)
    db.commit()
    _patch(client, org, p.id, 1, on_hand=2)
    main = client.get("/locations", headers=org.h()).json()[0]
    store = _loc(client, org, "S1")
    dup = client.post("/locations", json={"code": "s1", "name": "Again"}, headers=org.h())
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "location_exists"
    assert client.post("/locations", json={"code": "bad code", "name": "x"}, headers=org.h()).status_code == 422
    assert client.post("/locations", json={"code": "S2", "name": "x"}, headers=org.h("analyst")).status_code == 403

    deact = client.patch(f"/locations/{main['id']}", json={"expected_version": 1, "is_active": False}, headers=org.h())
    assert deact.status_code == 422  # the default cannot be deactivated
    # Make the store the default; MAIN still holds stock, so it cannot be deactivated either.
    r = client.patch(f"/locations/{store['id']}", json={"expected_version": 1, "is_default": True}, headers=org.h())
    assert r.status_code == 200 and r.json()["is_default"] is True
    locs = {loc["code"]: loc for loc in client.get("/locations", headers=org.h()).json()}
    assert locs["MAIN"]["is_default"] is False
    v = locs["MAIN"]["version"]
    assert (
        client.patch(
            f"/locations/{main['id']}", json={"expected_version": v, "is_active": False}, headers=org.h()
        ).status_code
        == 422
    )
    client.post(
        "/stock/transfers",
        json={"product_id": str(p.id), "from_location_id": main["id"], "to_location_id": store["id"], "quantity": 2},
        headers=org.h(),
    )
    r = client.patch(f"/locations/{main['id']}", json={"expected_version": v, "is_active": False}, headers=org.h())
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert [loc["code"] for loc in client.get("/locations", headers=org.h()).json()] == ["S1"]
    # With one active location again, counts without a location work.
    assert _patch(client, org, p.id, 3, on_hand=7).status_code == 200


def test_snapshot_upload_by_location(client, org, db):
    a, b = _product(db, org.org_id, "A"), _product(db, org.org_id, "B")
    db.commit()
    _loc(client, org, "S1")
    files = {"file": ("inv.csv", b"sku,on_hand\nA,5\n", "text/csv")}
    r = client.post("/inventory/upload", files=files, headers=org.h("operator"))
    assert r.status_code == 422 and "location" in r.json()["error"]["message"]
    csv_bytes = b"sku,on_hand,location,lead_time\nA,5,main,\nA,2,S1,\nB,1,NOPE,\nB,3,s1,999\n"
    r = client.post("/inventory/upload", files={"file": ("inv.csv", csv_bytes, "text/csv")}, headers=org.h("operator"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["updated"] == 2 and len(out["errors"]) == 2
    assert "unknown or inactive location" in out["errors"][0]["message"]
    assert out["errors"][1]["message"] == "lead time out of range"
    _, by_code = _stock(client, org, a.id)
    assert by_code == {"MAIN": (5, 0), "S1": (2, 0)}
    _, by_code = _stock(client, org, b.id)
    assert by_code == {"MAIN": (0, 0), "S1": (0, 0)}  # the bad rows applied nothing
    _assert_totals_match(db, a.id)
    _assert_totals_match(db, b.id)


def test_purchase_orders_deliver_to_a_location(client, org, db):
    p = _product(db, org.org_id, unit_cost=1.0)
    db.commit()
    _patch(client, org, p.id, 1, on_hand=1)
    store = _loc(client, org, "S1")
    sup = client.post("/suppliers", json={"name": "Acme"}, headers=org.h()).json()
    po = client.post(
        "/purchase-orders",
        json={
            "supplier_id": sup["id"],
            "location_id": store["id"],
            "lines": [{"product_id": str(p.id), "quantity": 8}],
        },
        headers=org.h("operator"),
    ).json()
    assert po["location_code"] == "S1"
    po = client.post(
        f"/purchase-orders/{po['id']}/approve", json={"expected_version": po["version"]}, headers=org.h("analyst")
    ).json()
    po = client.post(
        f"/purchase-orders/{po['id']}/send", json={"expected_version": po["version"]}, headers=org.h()
    ).json()
    _, by_code = _stock(client, org, p.id)
    assert by_code == {"MAIN": (1, 0), "S1": (0, 8)}
    po = client.post(
        f"/purchase-orders/{po['id']}/receive",
        json={"expected_version": po["version"], "lines": [{"product_id": str(p.id), "quantity": 3}]},
        headers=org.h(),
    ).json()
    _, by_code = _stock(client, org, p.id)
    assert by_code == {"MAIN": (1, 0), "S1": (3, 5)}
    client.post(f"/purchase-orders/{po['id']}/cancel", json={"expected_version": po["version"]}, headers=org.h())
    body, by_code = _stock(client, org, p.id)
    assert by_code == {"MAIN": (1, 0), "S1": (3, 0)} and (body["on_hand"], body["on_order"]) == (4, 0)
    _assert_totals_match(db, p.id)
    # An open order blocks deactivating its location.
    _ = client.post("/purchase-orders", json={"location_id": store["id"], "lines": []}, headers=org.h()).json()
    r = client.patch(f"/locations/{store['id']}", json={"expected_version": 1, "is_active": False}, headers=org.h())
    assert r.status_code == 422
