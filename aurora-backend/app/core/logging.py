"""Structured JSON logging with request correlation IDs."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
# Set by the Celery signals in app.workers.celery_app while a task runs.
task_var: ContextVar[tuple[str, str] | None] = ContextVar("celery_task", default=None)

# Attributes every LogRecord has; anything else on a record came from `extra=` and is logged as a field.
_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}

_REDACT_KEYS = {"password", "token", "secret", "authorization", "api_key", "refresh_token", "access_token"}


def redact(data: object) -> object:
    """Recursively replace values of sensitive-looking keys."""
    if isinstance(data, dict):
        return {
            k: ("[REDACTED]" if any(s in str(k).lower() for s in _REDACT_KEYS) else redact(v)) for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact(v) for v in data]
    return data


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        task = task_var.get()
        if task:
            payload["task_id"], payload["task_name"] = task
        plain = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS and k != "extra_fields"}
        if plain:  # logger.info("...", extra={"job_id": ...})
            payload.update(redact(plain))  # type: ignore[arg-type]
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(redact(extra))  # type: ignore[arg-type]
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root.handlers = [handler]
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, msg: str, level: int = logging.INFO, **fields: object) -> None:
    logger.log(level, msg, extra={"extra_fields": fields})
