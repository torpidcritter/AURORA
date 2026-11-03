"""Optional error reporting to Sentry or a Sentry-compatible service (ADR-048).

Nothing is sent unless SENTRY_DSN is set. Before an event leaves the process it is stripped of what could identify a
person or carry a secret: request bodies, cookies, query strings, most headers, the user's e-mail and IP address, and
local variables. Values under sensitive-looking keys are redacted as in the logs. The request id is attached, so an
error report can be matched to the JSON log lines and the audit log.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.logging import redact, request_id_var, task_var

log = logging.getLogger(__name__)

KEEP_HEADERS = {"user-agent", "content-type", "content-length", "x-request-id", "accept"}
_enabled = False


def scrub(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Remove personal data and secrets from an event. Also used for breadcrumbs' data."""
    req = event.get("request")
    if isinstance(req, dict):
        headers = req.get("headers") or {}
        req["headers"] = {k: v for k, v in headers.items() if str(k).lower() in KEEP_HEADERS}
        for k in ("cookies", "data", "query_string", "env"):
            req.pop(k, None)
        if isinstance(req.get("url"), str):
            req["url"] = req["url"].split("?", 1)[0]
    user = event.get("user")
    if isinstance(user, dict):
        event["user"] = {"id": user["id"]} if "id" in user else {}
    for frame_list in _frames(event):
        for frame in frame_list:
            frame.pop("vars", None)
    for key in ("extra", "contexts", "tags"):
        if isinstance(event.get(key), dict):
            event[key] = redact(event[key])
    crumbs = event.get("breadcrumbs")
    values = crumbs.get("values") if isinstance(crumbs, dict) else crumbs
    for c in values or []:
        if isinstance(c, dict) and isinstance(c.get("data"), dict):
            c["data"] = redact(c["data"])
    tags = event.setdefault("tags", {})
    rid = request_id_var.get()
    if rid:
        tags["request_id"] = rid
    task = task_var.get()
    if task:
        tags["celery_task"] = task[1]
    return event


def _frames(event: dict[str, Any]) -> list[list[dict]]:
    out = []
    for exc in (event.get("exception") or {}).get("values") or []:
        frames = (exc.get("stacktrace") or {}).get("frames")
        if frames:
            out.append(frames)
    for th in (event.get("threads") or {}).get("values") or []:
        frames = (th.get("stacktrace") or {}).get("frames")
        if frames:
            out.append(frames)
    return out


def init(component: str, *, transport: Any = None) -> bool:
    """Start error reporting for the API or the worker. Returns whether it is on."""
    global _enabled
    from app.core.config import get_settings

    s = get_settings()
    dsn = s.sentry_dsn.get_secret_value().strip() if s.sentry_dsn is not None else ""
    if not dsn:  # unset, or empty as docker compose passes it
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=s.environment,
        release=s.app_release or None,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=s.sentry_traces_sample_rate,
        before_send=scrub,
        before_send_transaction=scrub,
        transport=transport,
    )
    sentry_sdk.set_tag("component", component)
    _enabled = True
    log.info("error reporting enabled", extra={"component": component})
    return True


def enabled() -> bool:
    return _enabled
