"""Observability: health of jobs and webhooks in /metrics, alert rules and dashboard, error reporting (ADR-048)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from prometheus_client.parser import text_string_to_metric_families

from app.core import error_reporting, metrics
from app.db.models import Job, WebhookDelivery, WebhookEndpoint

ROOT = Path(__file__).resolve().parents[3]
MON = ROOT / "deploy" / "monitoring"
METRIC = re.compile(r"\baurora_[a-z_]+\b")


def _scrape(client) -> dict:
    metrics.STATE._at = 0.0
    text = client.get("/metrics").text
    families = list(text_string_to_metric_families(text))
    samples = {(s.name, tuple(sorted(s.labels.items()))): s.value for f in families for s in f.samples}
    return {"samples": samples, "families": {f.name for f in families}, "text": text}


def test_job_and_webhook_health_metrics(client, org, db):
    now = datetime.now(UTC)
    ep = WebhookEndpoint(org_id=org.org_id, url_encrypted="x", url_hint="https://h", secret_encrypted="x", events=[])
    db.add(ep)
    db.flush()
    db.add_all(
        [
            Job(org_id=org.org_id, kind="monitor", status="queued", created_at=now - timedelta(hours=1)),
            Job(org_id=org.org_id, kind="train", status="running", started_at=now - timedelta(hours=1)),
            Job(org_id=org.org_id, kind="batch_forecast", status="running", started_at=now, heartbeat_at=now),
            WebhookDelivery(
                org_id=org.org_id,
                endpoint_id=ep.id,
                event="ping",
                payload={},
                status="pending",
                next_attempt_at=now - timedelta(hours=1),
            ),
            WebhookDelivery(org_id=org.org_id, endpoint_id=ep.id, event="ping", payload={}, status="failed"),
        ]
    )
    db.commit()
    client.post("/chat", json={"message": "alerts?"}, headers=org.h("viewer"))
    s = _scrape(client)["samples"]
    assert s[("aurora_oldest_queued_job_age_seconds", ())] >= 3500
    assert s[("aurora_jobs_heartbeat_stale", ())] == 1  # only the job with no recent heartbeat
    assert s[("aurora_webhook_deliveries", (("status", "pending"),))] == 1
    assert s[("aurora_webhook_deliveries", (("status", "failed"),))] == 1
    assert s[("aurora_webhook_deliveries_overdue", ())] == 1
    assert s[("aurora_assistant_proposals_pending", ())] == 0
    assert s[("aurora_assistant_answers_total", (("mode", "fallback"),))] >= 1
    assert [v for k, v in s.items() if k[0] == "aurora_build_info"] == [1]


def _exprs() -> list[tuple[str, str]]:
    rules = yaml.safe_load((MON / "alerts.yml").read_text())
    out = [(r["alert"], r["expr"]) for g in rules["groups"] for r in g["rules"]]
    dash = json.loads((MON / "grafana-dashboard.json").read_text())
    out += [(p["title"], t["expr"]) for p in dash["panels"] for t in p["targets"]]
    return out


def test_rules_and_dashboard_use_only_exported_metrics(client, org):
    client.post("/chat", json={"message": "alerts?"}, headers=org.h("viewer"))  # labelled counters get a sample
    scraped = _scrape(client)
    known = {k[0] for k in scraped["samples"]} | scraped["families"]
    for where, expr in _exprs():
        for name in METRIC.findall(expr):
            base = re.sub(r"_(total|bucket|count|sum)$", "", name)
            assert name in known or base in scraped["families"], f"{where}: {name} is not exported by /metrics"


def test_alert_rules_are_complete_and_documented():
    rules = yaml.safe_load((MON / "alerts.yml").read_text())["groups"][0]["rules"]
    guide = (ROOT / "docs" / "OBSERVABILITY.md").read_text()
    anchors = {"#" + h.strip().lower() for h in re.findall(r"^### (\S+)$", guide, flags=re.M)}
    assert len(rules) >= 8
    for r in rules:
        assert r["labels"]["severity"] in ("page", "ticket"), r["alert"]
        assert r["annotations"]["summary"], r["alert"]
        link = r["annotations"]["runbook"]
        assert link.startswith("docs/OBSERVABILITY.md#") and link.split("md", 1)[1] in anchors, r["alert"]
    prom = yaml.safe_load((MON / "prometheus.yml").read_text())
    assert prom["rule_files"] == ["alerts.yml"]
    assert prom["scrape_configs"][0]["job_name"] == "aurora-api"  # the job name the rules select on
    assert "credentials" not in json.dumps(prom).replace("credentials_file", "")


def test_scrub_removes_personal_data_and_secrets():
    token = error_reporting.request_id_var.set("rid-123")
    try:
        event = error_reporting.scrub(
            {
                "request": {
                    "url": "https://api/products?q=secret-sku",
                    "headers": {"Authorization": "Bearer x", "Cookie": "aurora_at=x", "User-Agent": "ua"},
                    "cookies": {"aurora_at": "x"},
                    "data": {"password": "hunter2"},
                    "query_string": "q=secret-sku",
                },
                "user": {"id": "u1", "email": "a@example.com", "ip_address": "10.0.0.1"},
                "exception": {"values": [{"stacktrace": {"frames": [{"function": "f", "vars": {"password": "x"}}]}}]},
                "extra": {"api_key": "aur_x", "rows": 3},
                "breadcrumbs": {"values": [{"data": {"token": "t", "n": 1}}]},
            }
        )
    finally:
        error_reporting.request_id_var.reset(token)
    assert event["request"] == {"url": "https://api/products", "headers": {"User-Agent": "ua"}}
    assert event["user"] == {"id": "u1"}
    assert "vars" not in event["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert event["extra"] == {"api_key": "[REDACTED]", "rows": 3}
    assert event["breadcrumbs"]["values"][0]["data"] == {"token": "[REDACTED]", "n": 1}
    assert event["tags"]["request_id"] == "rid-123"


def test_error_reporting_is_off_without_dsn_and_scrubs_when_on(monkeypatch):
    from pydantic import SecretStr

    from app.core.config import get_settings

    assert error_reporting.init("api") is False
    monkeypatch.setattr(get_settings(), "sentry_dsn", SecretStr(" "))  # docker compose passes unset values as ""
    assert error_reporting.init("api") is False
    # Run the real SDK in a separate process so its global instrumentation cannot touch other tests.
    code = """
import json, sentry_sdk
from sentry_sdk.transport import Transport
from app.core import error_reporting
from app.core.logging import request_id_var
sent = []
class Capture(Transport):
    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.headers.get("type") == "event":
                sent.append(json.loads(item.payload.get_bytes()))
assert error_reporting.init("worker", transport=Capture()) is True
request_id_var.set("rid-9")
with sentry_sdk.new_scope() as scope:
    scope.set_extra("refresh_token", "abc")
    scope.set_user({"id": "u1", "email": "a@example.com"})
    try:
        raise ValueError("boom")
    except ValueError:
        sentry_sdk.capture_exception()
sentry_sdk.flush()
print(json.dumps(sent))
"""
    env = {**os.environ, "SENTRY_DSN": "https://public@sentry.invalid/1", "APP_RELEASE": "abc123"}
    res = subprocess.run(  # noqa: S603 - a fixed script run with this interpreter
        [sys.executable, "-c", code], cwd=ROOT / "aurora-backend", env=env, capture_output=True, text=True, timeout=60
    )
    assert res.returncode == 0, res.stderr[-2000:]
    [event] = json.loads(res.stdout.strip().splitlines()[-1])
    assert event["release"] == "abc123" and event["tags"]["component"] == "worker"
    assert event["tags"]["request_id"] == "rid-9"
    assert event["user"] == {"id": "u1"}
    assert event["extra"]["refresh_token"] == "[REDACTED]"
    frames = event["exception"]["values"][0]["stacktrace"]["frames"]
    assert all("vars" not in f for f in frames)
