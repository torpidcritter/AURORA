"""Live accuracy: served forecasts scored against the sales that arrived later."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from app.db.models import Alert, ForecastRun, Product, SalesRecord
from app.services import accuracy_service, monitoring_service

D0 = date(2026, 6, 1)


def _product(db, org_id, sku="SKU-A"):
    p = Product(org_id=org_id, sku=sku, name=sku)
    db.add(p)
    db.flush()
    return p


def _sales(db, org_id, pid, start, values):
    for i, q in enumerate(values):
        db.add(SalesRecord(org_id=org_id, product_id=pid, sale_date=start + timedelta(days=i), quantity=q))


def _run(db, org_id, pid, as_of, yhats, method="holt_winters"):
    points = [{"date": (as_of + timedelta(days=i + 1)).isoformat(), "yhat": y} for i, y in enumerate(yhats)]
    db.add(
        ForecastRun(
            org_id=org_id,
            product_id=pid,
            method=method,
            horizon=len(yhats),
            coverage=0.9,
            payload={"as_of": as_of.isoformat(), "points": points},
        )
    )


def test_freshest_forecast_is_scored_and_future_days_are_not(db, org):
    p = _product(db, org.org_id)
    _sales(db, org.org_id, p.id, D0, [10.0] * 10)  # actuals D0 .. D0+9
    _run(db, org.org_id, p.id, D0 + timedelta(days=4), [20.0] * 7)  # older: covers D0+5 .. D0+11
    _run(db, org.org_id, p.id, D0 + timedelta(days=6), [11.0] * 7)  # fresher: covers D0+7 .. D0+13
    db.commit()
    out = accuracy_service.live_accuracy(db, org.org_id, window_days=28)
    # D0+5, D0+6 from the older run (|20-10|), D0+7 .. D0+9 from the fresher one (|11-10|); D0+10.. not yet known
    assert out["points"] == 5
    assert out["wape"] == round(100 * (10 + 10 + 1 + 1 + 1) / 50, 2)
    assert out["sufficient"] is False  # fewer than MIN_POINTS


def test_zero_demand_days_are_left_out_of_mape_only(db, org):
    p = _product(db, org.org_id)
    _sales(db, org.org_id, p.id, D0, [10.0, 0.0, 10.0, 10.0])
    _run(db, org.org_id, p.id, D0 - timedelta(days=1), [10.0, 2.0, 10.0, 10.0])
    db.commit()
    out = accuracy_service.live_accuracy(db, org.org_id)
    assert out["mape"] == 0.0 and out["mape_excluded_zero_days"] == 1
    assert out["wape"] == round(100 * 2 / 30, 2)


def test_monitoring_alerts_on_poor_live_accuracy_and_resolves(db, org):
    p = _product(db, org.org_id)
    _sales(db, org.org_id, p.id, D0, [10.0] * 20)
    _run(db, org.org_id, p.id, D0 - timedelta(days=1), [16.0] * 20)  # 60% off every day
    db.commit()
    out = monitoring_service.run_for_org(db, org.org_id)
    assert out["live_accuracy"]["sufficient"] and out["live_accuracy"]["mape"] == 60.0
    alert = db.scalar(select(Alert).where(Alert.org_id == org.org_id, Alert.dedup_key == "model:live_accuracy"))
    assert alert is not None and alert.status != "resolved" and "SKU-A" in alert.message

    # A fresher, accurate forecast replaces the poor one for the same days: the alert clears.
    _run(db, org.org_id, p.id, D0, [10.0] * 19)
    _run(db, org.org_id, p.id, D0 - timedelta(days=1), [10.0])
    db.commit()
    monitoring_service.run_for_org(db, org.org_id)
    db.refresh(alert)
    assert alert.status == "resolved"


def test_accuracy_endpoint_is_viewer_readable_and_tenant_scoped(client, db, org, other_org):
    p = _product(db, org.org_id)
    _sales(db, org.org_id, p.id, D0, [5.0] * 3)
    _run(db, org.org_id, p.id, D0 - timedelta(days=1), [4.0] * 3)
    db.commit()
    mine = client.get("/intelligence/accuracy", headers=org.h("viewer")).json()
    assert mine["points"] == 3 and mine["by_product"][0]["sku"] == "SKU-A" and mine["alert_metric"] == "mape"
    theirs = client.get("/intelligence/accuracy", headers=other_org.h("viewer")).json()
    assert theirs["points"] == 0 and theirs["by_product"] == []
    assert client.get("/intelligence/accuracy?window_days=3", headers=org.h("viewer")).status_code == 422
