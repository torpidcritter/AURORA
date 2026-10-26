"""JSON log lines: extra fields, redaction and task context."""

from __future__ import annotations

import json
import logging

from app.core.logging import JsonFormatter, request_id_var, task_var


def _line(**extra: object) -> dict:
    rec = logging.getLogger("aurora.test").makeRecord("aurora.test", logging.INFO, __file__, 1, "hello", (), None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return json.loads(JsonFormatter().format(rec))


def test_plain_extra_fields_are_logged_and_secrets_redacted() -> None:
    out = _line(job_id="j-1", api_key="sk-live", attempt=2)
    assert out["msg"] == "hello" and out["job_id"] == "j-1" and out["attempt"] == 2
    assert out["api_key"] == "[REDACTED]"


def test_task_and_request_context_are_included() -> None:
    t1, t2 = task_var.set(("task-9", "aurora.run_job")), request_id_var.set("req-7")
    try:
        out = _line()
    finally:
        task_var.reset(t1)
        request_id_var.reset(t2)
    assert out["task_id"] == "task-9" and out["task_name"] == "aurora.run_job" and out["request_id"] == "req-7"
    assert "task_id" not in _line()
