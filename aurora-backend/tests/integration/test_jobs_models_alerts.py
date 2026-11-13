from __future__ import annotations

import io
import uuid

import pytest

from app.devtools.synthetic import generate_sales, to_csv_bytes


def load(client, org, n=3, days=200):
    df = generate_sales(n_products=n, days=days, intermittent=False)
    r = client.post(
        "/upload-dataset",
        files={"file": ("s.csv", io.BytesIO(to_csv_bytes(df)), "text/csv")},
        headers=org.h("operator"),
    )
    assert r.status_code == 201
    return {p["sku"]: p for p in client.get("/products", headers=org.h()).json()["items"]}


@pytest.mark.slow
def test_training_job_end_to_end_and_registry(client, org):
    prods = load(client, org)
    assert client.post("/train", json={"max_epochs": 2}, headers=org.h("operator")).status_code == 403
    r = client.post(
        "/train", json={"max_epochs": 3, "auto_promote": False}, headers={**org.h("analyst"), "Idempotency-Key": "t1"}
    )
    assert r.status_code == 202, r.text
    job = r.json()
    st = client.get(f"/train/{job['id']}/status", headers=org.h("viewer")).json()
    assert st["status"] == "succeeded", st
    assert st["epoch"] >= 1 and st["loss"] is not None and st["progress"] == 1.0
    res = st["result"]
    assert res["version"] == "v1.0.0" and res["promoted"] is False
    # same idempotency key returns the same job, no new training
    again = client.post(
        "/train", json={"max_epochs": 3, "auto_promote": False}, headers={**org.h("analyst"), "Idempotency-Key": "t1"}
    ).json()
    assert again["id"] == job["id"]
    reg = client.get("/models/registry", headers=org.h("viewer")).json()["items"]
    assert len(reg) == 1 and reg[0]["status"] == "candidate"
    vid = reg[0]["id"]

    # promotion: analyst cannot, admin can (force if gate failed)
    assert client.post(f"/models/registry/{vid}/promote", json={}, headers=org.h("analyst")).status_code == 403
    pr = client.post(
        f"/models/registry/{vid}/promote", json={"force": not reg[0]["gate_passed"]}, headers=org.h("admin")
    )
    assert pr.status_code == 200, pr.text
    assert pr.json()["status"] == "active"

    diag = client.get("/intelligence/diagnostics/active", headers=org.h("viewer")).json()
    assert diag["feature_importance"]["method"] == "permutation_importance"
    assert set(diag["interval_coverage"]) == {"0.8", "0.9", "0.95"}

    # transformer forecasting from the stored artifact
    pid = prods["SKU-001"]["id"]
    f = client.post(
        "/forecast", json={"product_id": pid, "method": "transformer", "horizon": 7}, headers=org.h()
    ).json()
    assert f["method"] == "transformer" and f["model_version"] == "v1.0.0"
    assert f["epistemic_uncertainty"]["method"] == "mc_dropout" and len(f["epistemic_uncertainty"]["std"]) == 7
    assert f["interval"]["calibrated"] is True
    too_long = client.post(
        "/forecast", json={"product_id": pid, "method": "transformer", "horizon": 14}, headers=org.h()
    )
    assert too_long.status_code == 422

    # second model + rollback
    r2 = client.post("/train", json={"max_epochs": 2, "auto_promote": False}, headers=org.h("analyst")).json()
    v2 = client.get(f"/jobs/{r2['id']}", headers=org.h()).json()["result"]["model_version_id"]
    client.post(f"/models/registry/{v2}/promote", json={"force": True}, headers=org.h("admin"))
    rb = client.post("/models/registry/rollback", headers=org.h("admin"))
    assert rb.status_code == 200 and rb.json()["version"] == "v1.0.0"
    statuses = {m["version"]: m["status"] for m in client.get("/models/registry", headers=org.h()).json()["items"]}
    assert statuses == {"v1.0.0": "active", "v1.1.0": "archived"}


def test_training_without_enough_history_fails_cleanly(client, org):
    load(client, org, n=2, days=40)
    r = client.post("/train", json={"max_epochs": 1}, headers=org.h("analyst")).json()
    j = client.get(f"/jobs/{r['id']}", headers=org.h()).json()
    assert j["status"] == "failed" and "enough history" in j["error"]
    # a failed job does not block a new one
    assert client.post("/train", json={"max_epochs": 1}, headers=org.h("analyst")).status_code == 202


def test_only_one_active_training_job(client, org, db):
    from app.db.models import Job

    db.add(Job(org_id=org.org_id, kind="train", status="running", params={}))
    db.commit()
    r = client.post("/train", json={"max_epochs": 1}, headers=org.h("analyst"))
    assert r.status_code == 409


def test_catalogue_jobs_are_not_stacked(client, org, other_org, db, monkeypatch):
    from app.db.models import Job
    from app.services import job_service

    sent = []

    def fake_send(kind, job_id, countdown=None):  # type: ignore[no-untyped-def]
        sent.append(kind)
        return type("Res", (), {"id": f"task-{job_id}"})()

    monkeypatch.setattr(job_service, "send", fake_send)
    for kind, path in (("batch_forecast", "/jobs/batch-forecast?horizon=14"), ("monitor", "/jobs/monitor")):
        active = Job(org_id=org.org_id, kind=kind, status="running", params={})
        db.add(active)
        db.commit()
        r = client.post(path, headers=org.h("analyst"))
        assert r.status_code == 202 and r.json()["id"] == str(active.id)  # the running job, not a new one
        other = client.post(path, headers=other_org.h("analyst")).json()  # other tenants are unaffected
        assert other["id"] != str(active.id)
    assert sent == ["batch_forecast", "monitor"]  # only the other organisation's jobs were queued


def test_batch_jobs_use_the_spec_task_name():
    from app.workers.celery_app import celery_app

    assert "aurora.run_batch_forecast_job" in celery_app.tasks


def test_duplicate_delivery_does_not_rerun(db, org):
    from app.db.models import Job
    from app.workers.tasks import run_job

    job = Job(org_id=org.org_id, kind="monitor", status="succeeded", params={})
    db.add(job)
    db.commit()
    assert run_job(str(job.id)) == "skipped"


def test_cancel_queued_job(client, org, db):
    from app.db.models import Job

    job = Job(org_id=org.org_id, kind="batch_forecast", status="queued", params={})
    db.add(job)
    db.commit()
    r = client.post(f"/jobs/{job.id}/cancel", headers=org.h("analyst"))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    from app.workers.tasks import run_job

    assert run_job(str(job.id)) == "skipped"
    assert client.post(f"/jobs/{job.id}/cancel", headers=org.h("analyst")).status_code == 409


def test_stale_job_reaper(db, org):
    from datetime import UTC, datetime, timedelta

    from app.db.models import Job
    from app.services.job_service import reap_stale

    job = Job(
        org_id=org.org_id,
        kind="monitor",
        status="running",
        params={},
        heartbeat_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(job)
    db.commit()
    assert reap_stale(db) == 1
    db.refresh(job)
    assert job.status == "failed" and "Abandoned" in job.error


def test_broker_down_marks_job_failed(client, org, monkeypatch):
    from app.services import job_service

    def boom(*a, **k):
        raise ConnectionError("broker down")

    monkeypatch.setattr(job_service, "send", boom)
    r = client.post("/jobs/batch-forecast", headers=org.h("analyst"))
    assert r.status_code == 503
    jobs = client.get("/jobs", headers=org.h()).json()["items"]
    assert jobs[0]["status"] == "failed" and "queue unavailable" in jobs[0]["error"]


def test_batch_forecast_job(client, org):
    load(client, org, n=2, days=90)
    r = client.post("/jobs/batch-forecast?horizon=14", headers=org.h("analyst")).json()
    j = client.get(f"/jobs/{r['id']}", headers=org.h()).json()
    assert j["status"] == "succeeded" and j["result"]["forecasts"] == 2


def test_monitoring_alerts_dedup_escalation_ack(client, org, db):
    prods = load(client, org, n=2, days=120)
    p = prods["SKU-001"]
    client.patch(
        f"/products/{p['id']}/inventory",
        json={"expected_version": p["version"], "on_hand": 30},
        headers=org.h("operator"),
    )
    for _ in range(2):  # re-running monitoring must not duplicate alerts
        r = client.post("/jobs/monitor", headers=org.h("analyst")).json()
        assert client.get(f"/jobs/{r['id']}", headers=org.h()).json()["status"] == "succeeded"
    alerts = client.get("/alerts?status=active&kind=stockout_risk", headers=org.h("viewer")).json()
    assert alerts["total"] == 1
    a = alerts["items"][0]
    assert a["severity"] in ("critical", "high")
    assert client.get("/alerts/summary", headers=org.h()).json()["open"] >= 1

    assert client.post(f"/alerts/{a['id']}/acknowledge", headers=org.h("viewer")).status_code == 403
    ack = client.post(f"/alerts/{a['id']}/acknowledge", headers=org.h("operator"))
    assert ack.status_code == 200 and ack.json()["status"] == "acknowledged" and ack.json()["acknowledged_at"]
    # acknowledging twice is idempotent
    assert client.post(f"/alerts/{a['id']}/acknowledge", headers=org.h("operator")).status_code == 200

    # condition clears -> auto-resolved
    p2 = client.get(f"/products/{p['id']}", headers=org.h()).json()
    client.patch(
        f"/products/{p['id']}/inventory",
        json={"expected_version": p2["version"], "on_hand": 100000},
        headers=org.h("operator"),
    )
    client.post("/jobs/monitor", headers=org.h("analyst"))
    assert client.get("/alerts?status=active&kind=stockout_risk", headers=org.h()).json()["total"] == 0

    assert client.delete(f"/alerts/{a['id']}", headers=org.h("analyst")).status_code == 403
    assert client.delete(f"/alerts/{a['id']}", headers=org.h("admin")).status_code == 204


def test_alert_upsert_escalates_never_downgrades(db, org):
    from app.db.models import Alert
    from app.services import alert_service

    kw = dict(org_id=org.org_id, kind="x", title="t", message="m", dedup_key="k1")
    a1 = alert_service.upsert(db, severity="medium", **kw)
    db.commit()
    a2 = alert_service.upsert(db, severity="critical", **kw)
    a3 = alert_service.upsert(db, severity="low", **kw)
    db.commit()
    assert a1 == a2 == a3
    assert db.get(Alert, a1).severity == "critical"


def test_alert_dispatch_is_idempotent(db, org, monkeypatch):
    from app.core.config import get_settings
    from app.db.models import AlertDelivery
    from app.services import alert_service

    monkeypatch.setattr(get_settings(), "alert_webhook_url", "https://hooks.example.com/aurora")
    sent = []

    class FakeClient:
        def post(self, url, json, headers):
            sent.append(json["id"])

            class R:
                def raise_for_status(self):
                    return None

            return R()

    alert_service.upsert(db, org_id=org.org_id, kind="x", severity="critical", title="t", message="m", dedup_key="d")
    db.commit()
    s1 = alert_service.dispatch(db, FakeClient())
    s2 = alert_service.dispatch(db, FakeClient())
    assert s1["delivered"] == 2 and s2["delivered"] == 0
    assert len(sent) == 1
    assert db.query(AlertDelivery).count() == 2


def test_alert_webhook_failure_retries_then_dead(db, org, monkeypatch):
    from app.core.config import get_settings
    from app.db.models import AlertDelivery
    from app.services import alert_service

    monkeypatch.setattr(get_settings(), "alert_webhook_url", "https://hooks.example.com/aurora")

    class Down:
        def post(self, *a, **k):
            raise ConnectionError("down")

    alert_service.upsert(db, org_id=org.org_id, kind="x", severity="high", title="t", message="m", dedup_key="d")
    db.commit()
    for _ in range(alert_service.MAX_DELIVERY_ATTEMPTS + 2):
        alert_service.dispatch(db, Down())
    row = db.query(AlertDelivery).filter_by(channel="webhook").one()
    assert row.status == "dead" and row.attempts == alert_service.MAX_DELIVERY_ATTEMPTS


def test_audit_log_access_and_contents(client, org, other_org):
    load(client, org, n=1, days=30)
    assert client.get("/audit", headers=org.h("operator")).status_code == 403
    r = client.get("/audit?action=dataset", headers=org.h("analyst")).json()
    assert r["total"] == 1 and r["items"][0]["action"] == "dataset.upload"
    assert client.get("/audit", headers=other_org.h("admin")).json()["total"] >= 0
    others = client.get("/audit?action=dataset", headers=other_org.h("admin")).json()
    assert others["total"] == 0


def test_ops_status(client, org):
    assert client.get("/ops/status", headers=org.h("operator")).status_code == 403
    r = client.get("/ops/status", headers=org.h("analyst")).json()
    assert r["components"]["database"]["ok"] is True
    assert "queues" in r


def test_cross_tenant_job_and_alert_access(client, org, other_org, db):
    from app.db.models import Alert, Job

    job = Job(org_id=org.org_id, kind="monitor", status="queued", params={})
    al = Alert(org_id=org.org_id, kind="x", severity="high", title="t", dedup_key="z")
    db.add_all([job, al])
    db.commit()
    assert client.get(f"/jobs/{job.id}", headers=other_org.h()).status_code == 404
    assert client.post(f"/jobs/{job.id}/cancel", headers=other_org.h()).status_code == 404
    assert client.post(f"/alerts/{al.id}/acknowledge", headers=other_org.h()).status_code == 404
    assert client.delete(f"/alerts/{al.id}", headers=other_org.h()).status_code == 404
    assert client.get(f"/models/registry/{uuid.uuid4()}", headers=other_org.h()).status_code == 404


def test_model_age_is_measured_from_the_checkpoint_not_the_promotion(db, org):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.db.models import Alert, ModelVersion
    from app.services import monitoring_service

    now = datetime.now(UTC)
    # A 60-day-old checkpoint that was promoted (e.g. by a rollback) a minute ago is still stale.
    mv = ModelVersion(
        org_id=org.org_id,
        version="v1.0.0",
        status="active",
        artifact_key="models/x.pt",
        checksum="0" * 64,
        config={},
        metrics={},
        training_meta={},
        promoted_at=now,
        gate_passed=True,
        created_at=now - timedelta(days=60),
    )
    db.add(mv)
    db.commit()
    monitoring_service.run_for_org(db, org.org_id)
    stale = db.scalars(select(Alert).where(Alert.org_id == org.org_id, Alert.kind == "model_stale")).all()
    assert len(stale) == 1 and stale[0].details["age_days"] == 60


def test_job_runs_with_the_request_id_that_started_it(client, org, monkeypatch):
    from app.core.logging import request_id_var
    from app.workers import tasks

    seen = []

    def fake_batch(db, job):  # type: ignore[no-untyped-def]
        seen.append(request_id_var.get())
        return {"forecasts": 0, "failed": [], "horizon": 14}

    monkeypatch.setattr(tasks, "_batch_forecast", fake_batch)
    r = client.post("/jobs/batch-forecast?horizon=14", headers=org.h("analyst") | {"X-Request-ID": "req-batch-1"})
    assert r.status_code == 202 and r.headers["X-Request-ID"] == "req-batch-1"
    assert seen == ["req-batch-1"]
