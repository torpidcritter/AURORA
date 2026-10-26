"""Liveness, readiness and Prometheus endpoints."""

from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST
from sqlalchemy import text

from app.core.config import get_settings
from app.core.errors import Unauthorized
from app.core.metrics import render as metrics_render
from app.core.redis_client import get_redis
from app.db.session import get_engine
from app.storage import get_storage

router = APIRouter(tags=["health"])
STARTED = time.time()


def check_components() -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    t = time.perf_counter()
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
        out["database"] = {"ok": True, "latency_ms": round((time.perf_counter() - t) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        out["database"] = {"ok": False, "error": type(exc).__name__}
    t = time.perf_counter()
    try:
        get_redis().ping()
        out["redis"] = {"ok": True, "latency_ms": round((time.perf_counter() - t) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        out["redis"] = {"ok": False, "error": type(exc).__name__}
    try:
        out["storage"] = {"ok": bool(get_storage().healthcheck())}
    except Exception as exc:  # noqa: BLE001
        out["storage"] = {"ok": False, "error": type(exc).__name__}
    return out


@router.get("/health/live")
def live() -> dict[str, str]:
    """Liveness: the process is up. Never depends on downstream services."""
    return {"status": "alive"}


@router.get("/health/ready")
def ready() -> JSONResponse:
    """Readiness: database and storage are required to serve; Redis is required to enqueue jobs."""
    comps = check_components()
    ok = all(c["ok"] for c in comps.values())
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "not_ready", "components": {k: v["ok"] for k, v in comps.items()}},
    )


@router.get("/health")
def health() -> dict[str, object]:
    """Public summary: no versions, hostnames or error strings."""
    comps = check_components()
    return {
        "status": "ok" if all(c["ok"] for c in comps.values()) else "degraded",
        "uptime_s": int(time.time() - STARTED),
        "components": {k: "ok" if v["ok"] else "down" for k, v in comps.items()},
    }


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request) -> Response:
    token = get_settings().metrics_token
    if token is not None:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied, token.get_secret_value()):
            raise Unauthorized("metrics token required")
    return Response(metrics_render(), media_type=CONTENT_TYPE_LATEST)
