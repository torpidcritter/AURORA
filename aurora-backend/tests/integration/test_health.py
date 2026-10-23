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
