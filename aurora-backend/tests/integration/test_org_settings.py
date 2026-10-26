"""Organisation settings: validation, RBAC, tenant isolation, audit, locking, and their effect on inventory
recommendations, monitoring and alert dispatch."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db.models import (
    Alert,
    AlertDelivery,
    AuditEvent,
    ForecastRun,
    ModelVersion,
    Organization,
    Product,
    SalesRecord,
)
from app.db.session import get_sessionmaker
from app.services import alert_service, monitoring_service, settings_service

KEYS = [
    "default_lead_time_days",
    "default_coverage_days",
    "default_service_level",
    "drift_ks_threshold",
    "drift_mean_change_threshold",
    "model_max_age_days",
    "accuracy_live_metric",
    "accuracy_live_threshold",
    "alert_min_severity",
]


def items(resp) -> dict[str, dict]:
    assert resp.status_code == 200, resp.text
    return {i["key"]: i for i in resp.json()["items"]}


def patch(client, org, body, role="admin"):
    return client.patch("/settings/org", json=body, headers=org.h(role))


def stored(db, org_id) -> dict:
    return db.execute(select(Organization.settings).where(Organization.id == org_id)).scalar_one()


def product(db, org_id, sku="SKU-A", **kw) -> Product:
    p = Product(org_id=org_id, sku=sku, name=sku, **kw)
    db.add(p)
    db.flush()
    return p


def sales(db, org_id, pid, start: date, values) -> None:
    for i, q in enumerate(values):
        db.add(SalesRecord(org_id=org_id, product_id=pid, sale_date=start + timedelta(days=i), quantity=q))


# --- reading ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["viewer", "operator", "analyst", "admin"])
def test_every_role_reads_the_effective_defaults(client, org, role):
    s = get_settings()
    got = items(client.get("/settings/org", headers=org.h(role)))
    assert list(got) == KEYS
    for k, item in got.items():
        assert item["value"] == item["default"] == getattr(s, k)
        assert item["source"] == "default" and item["override"] is None
        assert item["label"] and item["description"]
    assert (
        got["default_lead_time_days"] | {"type": "integer", "minimum": 1, "maximum": 365}
        == got["default_lead_time_days"]
    )
    sl = got["default_service_level"]
    assert sl["type"] == "number" and sl["minimum"] == 0.5 and sl["minimum_exclusive"] and sl["maximum"] == 0.999
    assert got["alert_min_severity"]["type"] == "choice"
    assert got["alert_min_severity"]["choices"] == ["low", "medium", "high", "critical"]
    assert got["alert_min_severity"]["default"] == "high"  # unchanged historical rule: high and critical
    assert client.get("/settings/org", headers=org.h(role)).json()["alert_channels"] == ["log"]


def test_reading_requires_authentication(client):
    assert client.get("/settings/org").status_code == 401
    assert client.patch("/settings/org", json={"default_lead_time_days": 3}).status_code == 401


def test_alert_channels_show_the_webhook_without_its_address(client, org, monkeypatch):
    monkeypatch.setattr(get_settings(), "alert_webhook_url", "https://hooks.example.com/secret-path")
    r = client.get("/settings/org", headers=org.h("viewer"))
    assert r.json()["alert_channels"] == ["log", "webhook"]
    assert "hooks.example.com" not in r.text


# --- updating --------------------------------------------------------------------------------------


def test_admin_partial_update_and_clear(client, org, db):
    got = items(patch(client, org, {"default_lead_time_days": 10, "accuracy_live_metric": "wape"}))
    assert (
        got["default_lead_time_days"] | {"value": 10, "override": 10, "source": "org", "default": 7}
        == (got["default_lead_time_days"])
    )
    assert got["accuracy_live_metric"]["value"] == "wape" and got["accuracy_live_metric"]["source"] == "org"
    assert got["model_max_age_days"]["source"] == "default"

    # keys left out are unchanged; null clears one override only
    got = items(patch(client, org, {"default_lead_time_days": None, "model_max_age_days": 14}))
    assert got["default_lead_time_days"]["source"] == "default" and got["default_lead_time_days"]["value"] == 7
    assert got["accuracy_live_metric"]["value"] == "wape"
    assert got["model_max_age_days"]["value"] == 14
    assert stored(db, org.org_id) == {"accuracy_live_metric": "wape", "model_max_age_days": 14}
    assert items(client.get("/settings/org", headers=org.h("viewer")))["model_max_age_days"]["value"] == 14


@pytest.mark.parametrize("role", ["viewer", "operator", "analyst"])
def test_non_admins_cannot_change_settings(client, org, db, role):
    r = patch(client, org, {"default_lead_time_days": 10}, role=role)
    assert r.status_code == 403
    assert stored(db, org.org_id) == {}


BOUNDS = [
    ("default_lead_time_days", 1, 365, 0, 366),
    ("default_coverage_days", 1, 365, 0, 366),
    ("default_service_level", 0.501, 0.999, 0.5, 1.0),
    ("drift_ks_threshold", 0.01, 1, 0.009, 1.01),
    ("drift_mean_change_threshold", 0.01, 5, 0.0, 5.5),
    ("model_max_age_days", 1, 365, 0, 400),
    ("accuracy_live_threshold", 1, 500, 0.5, 501),
]


@pytest.mark.parametrize("key,lo,hi,below,above", BOUNDS, ids=[b[0] for b in BOUNDS])
def test_bounds_are_enforced(client, org, db, key, lo, hi, below, above):
    for bad in (below, above):
        r = patch(client, org, {key: bad})
        assert r.status_code == 422, (key, bad, r.text)
        err = r.json()["error"]
        assert err["code"] == "validation_error" and err["details"][0]["loc"][-1] == key
    assert stored(db, org.org_id) == {}
    for ok in (lo, hi):
        assert items(patch(client, org, {key: ok}))[key]["value"] == ok


@pytest.mark.parametrize(
    "body",
    [
        {"default_lead_time_days": "7"},  # strings are not coerced
        {"default_lead_time_days": True},
        {"default_lead_time_days": 7.5},
        {"model_max_age_days": 30.0},  # integers only
        {"default_service_level": "0.95"},
        {"drift_ks_threshold": False},
        {"accuracy_live_metric": "MAPE"},
        {"accuracy_live_metric": "rmse"},
        {"alert_min_severity": "urgent"},
        {"alert_min_severity": 2},
    ],
)
def test_wrong_types_and_choices_are_rejected(client, org, db, body):
    assert patch(client, org, body).status_code == 422
    assert stored(db, org.org_id) == {}


def test_unknown_keys_are_rejected_and_nothing_is_applied(client, org, db):
    r = patch(client, org, {"default_lead_time_days": 10, "alert_webhook_url": "https://evil.example.com"})
    assert r.status_code == 422
    assert r.json()["error"]["details"][0]["loc"][-1] == "alert_webhook_url"
    assert patch(client, org, {"accuracy_wape_threshold": 10}).status_code == 422  # global-only setting
    assert patch(client, org, {"default_lead_time_days": 10, "model_max_age_days": 0}).status_code == 422
    assert patch(client, org, ["default_lead_time_days"]).status_code == 422
    assert stored(db, org.org_id) == {}


def test_settings_are_isolated_between_organisations(client, org, other_org, db):
    patch(client, org, {"default_lead_time_days": 21, "alert_min_severity": "low"})
    theirs = items(client.get("/settings/org", headers=other_org.h("admin")))
    assert all(i["source"] == "default" for i in theirs.values())
    assert settings_service.for_org(db, other_org.org_id).default_lead_time_days == 7
    assert settings_service.for_org(db, org.org_id).default_lead_time_days == 21
    # and the other org's own change does not leak back
    patch(client, other_org, {"default_lead_time_days": 3})
    assert items(client.get("/settings/org", headers=org.h("viewer")))["default_lead_time_days"]["value"] == 21


def test_update_is_audited_with_before_and_after(client, org, other_org, db):
    patch(client, org, {"default_lead_time_days": 10})
    patch(client, org, {"default_lead_time_days": 12, "alert_min_severity": "medium"})
    patch(client, org, {"default_lead_time_days": 12})  # no change: not audited
    patch(client, org, {})  # empty body: no change
    rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.org_id == org.org_id, AuditEvent.action == "settings.updated")
        .order_by(AuditEvent.id)
    ).all()
    assert len(rows) == 2
    last = rows[-1]
    assert last.actor_email == org.emails["admin"] and last.resource_type == "organization"
    assert last.resource_id == str(org.org_id) and last.outcome == "success"
    assert last.details == {
        "changed": ["default_lead_time_days", "alert_min_severity"],
        "before": {"default_lead_time_days": 10},
        "after": {"default_lead_time_days": 12, "alert_min_severity": "medium"},
    }
    # visible in the audit log API to the org, not to another org
    log = client.get("/audit?action=settings.", headers=org.h("analyst")).json()
    assert log["total"] == 2
    assert client.get("/audit?action=settings.", headers=other_org.h("admin")).json()["total"] == 0
    # a refused update is not audited as a change
    patch(client, org, {"default_lead_time_days": 10}, role="analyst")
    assert db.query(AuditEvent).filter_by(org_id=org.org_id, action="settings.updated").count() == 2


def test_other_keys_in_the_settings_document_are_preserved_and_bad_values_ignored(client, org, db):
    o = db.get(Organization, org.org_id)
    o.settings = {"shopify_sync": {"enabled": True}, "default_lead_time_days": "abc", "alert_min_severity": "urgent"}
    db.commit()
    got = items(client.get("/settings/org", headers=org.h("viewer")))
    assert got["default_lead_time_days"]["source"] == "default" and got["default_lead_time_days"]["value"] == 7
    assert got["alert_min_severity"]["value"] == "high"
    patch(client, org, {"model_max_age_days": 9})
    # the foreign key survives, the invalid owned values are dropped on write
    assert stored(db, org.org_id) == {"shopify_sync": {"enabled": True}, "model_max_age_days": 9}


def test_service_validates_too(db, org):
    from app.core.errors import ValidationFailed

    with pytest.raises(ValidationFailed):
        settings_service.update(db, org.org_id, {"default_lead_time_days": 0})
    with pytest.raises(ValidationFailed):
        settings_service.update(db, org.org_id, {"unknown": 1})
    db.rollback()


def test_concurrent_updates_do_not_lose_each_other(org):
    """The first update holds the organisation row lock until it commits; the second waits and then merges."""
    sm = get_sessionmaker()
    first = sm()
    second_done = threading.Event()
    errors: list[BaseException] = []

    def second() -> None:
        s = sm()
        try:
            settings_service.update(s, org.org_id, {"model_max_age_days": 5})
            s.commit()
            second_done.set()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test below
            errors.append(exc)
        finally:
            s.close()

    try:
        settings_service.update(first, org.org_id, {"default_lead_time_days": 9})
        t = threading.Thread(target=second)
        t.start()
        assert not second_done.wait(0.7), "second update did not wait for the row lock"
        first.commit()
        t.join(15)
        assert not errors and second_done.is_set()
    finally:
        first.close()
    check = sm()
    try:
        eff = settings_service.for_org(check, org.org_id)
        assert eff.default_lead_time_days == 9 and eff.model_max_age_days == 5
    finally:
        check.close()


def test_global_alert_min_severity_setting():
    assert Settings(_env_file=None).alert_min_severity == "high"  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, alert_min_severity="urgent")  # type: ignore[call-arg]


# --- effect on inventory recommendations -----------------------------------------------------------


def _stocked_product(db, org_id, **kw) -> Product:
    p = product(db, org_id, on_hand=40, inventory_known=True, **kw)
    sales(db, org_id, p.id, date(2026, 3, 1), [10.0, 12.0, 8.0, 11.0, 9.0, 10.0, 10.0] * 13)
    db.commit()
    return p


def test_recommendation_uses_the_organisation_defaults(client, org, other_org, db):
    p = _stocked_product(db, org.org_id)
    own = _stocked_product(db, org.org_id, sku="SKU-OWN", lead_time_days=4, service_level=0.9)
    theirs = _stocked_product(db, other_org.org_id)

    def rec(o, pid, **body):
        r = client.post("/inventory/recommendation", json={"product_id": str(pid), **body}, headers=o.h("viewer"))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ok", r.json()
        return r.json()

    before = rec(org, p.id)
    assert (before["lead_time_days"], before["coverage_days"], before["service_level"]) == (7, 14, 0.95)
    assert "default of 7 days assumed" in " ".join(before["assumptions"])

    patch(client, org, {"default_lead_time_days": 21, "default_coverage_days": 30, "default_service_level": 0.975})
    after = rec(org, p.id)
    assert (after["lead_time_days"], after["coverage_days"], after["service_level"]) == (21, 30, 0.975)
    text = " ".join(after["assumptions"])
    assert "organisation default of 21 days assumed" in text and "organisation default 97.5%" in text
    assert after["reorder_point"] > before["reorder_point"]
    assert after["reorder_point"] == pytest.approx(after["mean_daily_demand"] * 21 + after["safety_stock"], rel=1e-3)

    # a product's own values and a request's explicit values still win over the organisation defaults
    mine = rec(org, own.id)
    assert (mine["lead_time_days"], mine["service_level"], mine["coverage_days"]) == (4, 0.9, 30)
    assert rec(org, p.id, lead_time_days=2, coverage_days=5)["lead_time_days"] == 2
    # the risk matrix (batch) and the stockout endpoint use the same defaults
    batch = client.post("/risk/stockout/batch", json={}, headers=org.h("viewer")).json()["items"]
    assert {r["sku"]: r["lead_time_days"] for r in batch} == {"SKU-A": 21, "SKU-OWN": 4}
    stock = client.post("/risk/stockout", json={"product_id": str(p.id)}, headers=org.h("viewer")).json()
    assert stock["lead_time_days"] == 21

    # another organisation is unaffected
    other = rec(other_org, theirs.id)
    assert (other["lead_time_days"], other["coverage_days"], other["service_level"]) == (7, 14, 0.95)


# --- effect on monitoring --------------------------------------------------------------------------


def _model(db, org_id, age_days: int) -> None:
    now = datetime.now(UTC)
    db.add(
        ModelVersion(
            org_id=org_id,
            version="v1.0.0",
            status="active",
            artifact_key=f"models/{uuid.uuid4().hex}.pt",
            checksum="0" * 64,
            config={},
            metrics={},
            training_meta={},
            promoted_at=now,
            gate_passed=True,
            created_at=now - timedelta(days=age_days),
        )
    )
    db.commit()


def _alerts(db, org_id, kind=None, key=None) -> list[Alert]:
    q = select(Alert).where(Alert.org_id == org_id, Alert.status != "resolved")
    if kind:
        q = q.where(Alert.kind == kind)
    if key:
        q = q.where(Alert.dedup_key == key)
    return list(db.scalars(q).all())


def test_model_age_threshold_is_per_organisation(client, org, other_org, db):
    _model(db, org.org_id, 20)
    _model(db, other_org.org_id, 20)
    monitoring_service.run_for_org(db, org.org_id)
    assert _alerts(db, org.org_id, "model_stale") == []  # 20 days < global 30

    patch(client, org, {"model_max_age_days": 10})
    monitoring_service.run_for_org(db, org.org_id)
    monitoring_service.run_for_org(db, other_org.org_id)
    stale = _alerts(db, org.org_id, "model_stale")
    assert len(stale) == 1 and stale[0].details["max_age_days"] == 10 and "after 10 days" in stale[0].message
    assert _alerts(db, other_org.org_id, "model_stale") == []

    # clearing the override restores the global threshold and the alert auto-resolves
    patch(client, org, {"model_max_age_days": None})
    monitoring_service.run_for_org(db, org.org_id)
    assert _alerts(db, org.org_id, "model_stale") == []


def _drifting(db, org_id) -> Product:
    p = product(db, org_id, sku="DRIFT")
    # 60 baseline days around 11, then 30 days around 14: KS statistic 1.0, mean change ~27%
    sales(db, org_id, p.id, date(2026, 1, 1), [10.0, 12.0] * 30 + [13.0, 15.0] * 15)
    db.commit()
    return p


def test_drift_thresholds_are_per_organisation(client, org, other_org, db):
    _drifting(db, org.org_id)
    _drifting(db, other_org.org_id)
    monitoring_service.run_for_org(db, org.org_id)
    assert len(_alerts(db, org.org_id, "demand_drift")) == 1
    assert client.get("/intelligence/drift", headers=org.h("viewer")).json()["drifting"] == 1

    patch(client, org, {"drift_ks_threshold": 1, "drift_mean_change_threshold": 0.5})
    monitoring_service.run_for_org(db, org.org_id)
    assert _alerts(db, org.org_id, "demand_drift") == []  # auto-resolved under the looser thresholds
    d = client.get("/intelligence/drift", headers=org.h("viewer")).json()
    assert d["drifting"] == 0 and d["items"][0]["thresholds"] == {"ks": 1, "mean_change": 0.5}

    # the other organisation still uses the global thresholds
    monitoring_service.run_for_org(db, other_org.org_id)
    assert len(_alerts(db, other_org.org_id, "demand_drift")) == 1
    assert client.get("/intelligence/drift", headers=other_org.h("viewer")).json()["drifting"] == 1


def test_assistant_drift_tool_uses_the_organisation_thresholds(client, org, db):
    from app.api.deps import Principal
    from app.services import assistant_service

    _drifting(db, org.org_id)
    viewer = Principal(user_id=org.users["viewer"], org_id=org.org_id, role="viewer", email="v@example.com", ip=None)
    assert assistant_service.run_tool(db, viewer, "get_drift_status", {})["drifting_count"] == 1
    patch(client, org, {"drift_ks_threshold": 1, "drift_mean_change_threshold": 0.5})
    assert assistant_service.run_tool(db, viewer, "get_drift_status", {})["drifting_count"] == 0


def _poor_live_accuracy(db, org_id) -> None:
    p = product(db, org_id)
    d0 = date(2026, 6, 1)
    sales(db, org_id, p.id, d0, [10.0] * 20)
    points = [{"date": (d0 + timedelta(days=i)).isoformat(), "yhat": 14.0} for i in range(20)]  # 40% off every day
    db.add(
        ForecastRun(
            org_id=org_id,
            product_id=p.id,
            method="holt_winters",
            horizon=20,
            coverage=0.9,
            payload={"as_of": (d0 - timedelta(days=1)).isoformat(), "points": points},
        )
    )
    db.commit()


def test_live_accuracy_metric_and_threshold_are_per_organisation(client, org, other_org, db):
    _poor_live_accuracy(db, org.org_id)
    _poor_live_accuracy(db, other_org.org_id)
    key = "model:live_accuracy"
    monitoring_service.run_for_org(db, org.org_id)
    assert len(_alerts(db, org.org_id, key=key)) == 1  # MAPE 40 > global 25

    patch(client, org, {"accuracy_live_threshold": 50})
    monitoring_service.run_for_org(db, org.org_id)
    assert _alerts(db, org.org_id, key=key) == []
    acc = client.get("/intelligence/accuracy", headers=org.h("viewer")).json()
    assert acc["alert_metric"] == "mape" and acc["alert_threshold"] == 50

    patch(client, org, {"accuracy_live_metric": "wape", "accuracy_live_threshold": 30})
    monitoring_service.run_for_org(db, org.org_id)
    (alert,) = _alerts(db, org.org_id, key=key)
    assert alert.details["metric"] == "wape" and alert.details["threshold"] == 30
    assert alert.title.startswith("Live forecast WAPE 40.0% exceeds 30%")

    monitoring_service.run_for_org(db, other_org.org_id)
    (theirs,) = _alerts(db, other_org.org_id, key=key)
    assert theirs.details["metric"] == "mape" and theirs.details["threshold"] == 25.0
    acc = client.get("/intelligence/accuracy", headers=other_org.h("viewer")).json()
    assert acc["alert_metric"] == "mape" and acc["alert_threshold"] == 25.0


# --- effect on alert dispatch ----------------------------------------------------------------------

SEVERITIES = ("low", "medium", "high", "critical")


def _one_of_each(db, org_id) -> None:
    for sev in SEVERITIES:
        alert_service.upsert(
            db, org_id=org_id, kind="x", severity=sev, title=f"{sev} alert", message="m", dedup_key=f"k:{sev}"
        )
    db.commit()


def _delivered(db, org_id) -> set[str]:
    rows = db.execute(
        select(Alert.severity)
        .join(AlertDelivery, AlertDelivery.alert_id == Alert.id)
        .where(Alert.org_id == org_id, AlertDelivery.status == "delivered", AlertDelivery.channel == "log")
    ).all()
    return {r[0] for r in rows}


def test_dispatch_default_is_high_and_critical(db, org):
    _one_of_each(db, org.org_id)
    alert_service.dispatch(db)
    assert _delivered(db, org.org_id) == {"high", "critical"}


def test_dispatch_honours_each_organisations_minimum_severity(client, db, org, other_org, monkeypatch):
    patch(client, org, {"alert_min_severity": "medium"})
    patch(client, other_org, {"alert_min_severity": "critical"})
    _one_of_each(db, org.org_id)
    _one_of_each(db, other_org.org_id)
    alert_service.dispatch(db)
    assert _delivered(db, org.org_id) == {"medium", "high", "critical"}
    assert _delivered(db, other_org.org_id) == {"critical"}

    # webhook routing follows the same rule (both channels see the same alerts)
    monkeypatch.setattr(get_settings(), "alert_webhook_url", "https://hooks.example.com/aurora")
    sent: list[tuple[str, str]] = []

    class FakeClient:
        def post(self, url, json, headers):
            sent.append((json["org_id"], json["severity"]))

            class R:
                def raise_for_status(self):
                    return None

            return R()

    alert_service.dispatch(db, FakeClient())
    assert sorted(sent) == sorted(
        [(str(org.org_id), s) for s in ("medium", "high", "critical")] + [(str(other_org.org_id), "critical")]
    )


def test_global_minimum_applies_to_organisations_without_an_override(client, db, org, other_org, monkeypatch):
    monkeypatch.setattr(get_settings(), "alert_min_severity", "low")
    patch(client, other_org, {"alert_min_severity": "critical"})
    _one_of_each(db, org.org_id)
    _one_of_each(db, other_org.org_id)
    alert_service.dispatch(db)
    assert _delivered(db, org.org_id) == set(SEVERITIES)
    assert _delivered(db, other_org.org_id) == {"critical"}


def test_invalid_stored_minimum_falls_back_to_the_global_one(db, org):
    o = db.get(Organization, org.org_id)
    o.settings = {"alert_min_severity": "urgent"}
    db.commit()
    _one_of_each(db, org.org_id)
    alert_service.dispatch(db)
    assert _delivered(db, org.org_id) == {"high", "critical"}


def test_lowering_the_minimum_delivers_waiting_alerts_once(client, db, org):
    _one_of_each(db, org.org_id)
    alert_service.dispatch(db)
    patch(client, org, {"alert_min_severity": "low"})
    stats = alert_service.dispatch(db)
    assert stats["delivered"] == 2  # low and medium now; high and critical were already delivered
    assert _delivered(db, org.org_id) == set(SEVERITIES)
    assert db.query(AlertDelivery).count() == 4
