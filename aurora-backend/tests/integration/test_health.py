def test_live_ready_health_metrics(client):
    assert client.get("/health/live").json() == {"status": "alive"}
    r = client.get("/health/ready")
    assert r.status_code == 200, r.text
    assert r.json()["components"] == {"database": True, "redis": True, "storage": True}
    assert client.get("/health").json()["status"] == "ok"
    m = client.get("/metrics")
    assert m.status_code == 200
    assert "aurora_http_requests_total" in m.text
    # route templates, not raw paths
    assert 'path="/health/live"' in m.text


def test_request_id_propagation(client):
    r = client.get("/health/live", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"
    r = client.get("/health/live", headers={"X-Request-ID": "bad id with spaces!"})
    assert r.headers["X-Request-ID"] != "bad id with spaces!"


def test_error_envelope(client):
    r = client.get("/does-not-exist")
    assert r.status_code == 404
    assert set(r.json()["error"]) == {"code", "message", "details", "request_id"}


def test_state_metrics_come_from_the_database(client, org, db):
    """Job outcomes, open alerts and training time are produced in the Celery worker; /metrics reads them from the
    database, so they are right even though the worker process exports nothing itself."""
    from datetime import UTC, datetime, timedelta

    from prometheus_client.parser import text_string_to_metric_families

    from app.core import metrics
    from app.db.models import Alert, Job

    now = datetime.now(UTC)
    db.add_all(
        [
            Job(
                org_id=org.org_id,
                kind="train",
                status="succeeded",
                started_at=now - timedelta(seconds=90),
                finished_at=now,
            ),
            Job(org_id=org.org_id, kind="batch_forecast", status="failed", started_at=now, finished_at=now),
            Job(org_id=org.org_id, kind="monitor", status="queued"),
            Alert(
                org_id=org.org_id,
                kind="demand_drift",
                severity="medium",
                status="open",
                dedup_key="d:1",
                title="t",
                message="m",
            ),
            Alert(
                org_id=org.org_id,
                kind="demand_drift",
                severity="medium",
                status="resolved",
                dedup_key="d:2",
                title="t",
                message="m",
            ),
        ]
    )
    db.commit()
    metrics.STATE._at = 0.0  # drop the short scrape cache
    text = client.get("/metrics").text
    samples = {
        (s.name, tuple(sorted(s.labels.items()))): s.value
        for fam in text_string_to_metric_families(text)
        for s in fam.samples
    }
    assert samples[("aurora_forecast_jobs_total", (("kind", "train"), ("status", "succeeded")))] == 1
    assert samples[("aurora_forecast_jobs_total", (("kind", "batch_forecast"), ("status", "failed")))] == 1
    assert samples[("aurora_jobs_in_progress", (("kind", "monitor"),))] == 1
    assert samples[("aurora_active_alerts_total", ())] == 1
    assert samples[("aurora_training_duration_seconds_bucket", (("le", "60"),))] == 0
    assert samples[("aurora_training_duration_seconds_bucket", (("le", "120"),))] == 1
    assert samples[("aurora_training_duration_seconds_count", ())] == 1
    assert ("aurora_queue_depth", (("queue", "default"),)) in samples
