"""Consistent error envelope: {"error": {"code", "message", "details", "request_id"}}."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

logger = logging.getLogger("aurora.errors")


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: Any = None, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_error"


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"


class Unavailable(AppError):
    status_code = 503
    code = "unavailable"


def _envelope(status: int, code: str, message: str, details: Any = None, headers: dict | None = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details, "request_id": request_id_var.get()}}
    return JSONResponse(status_code=status, content=body, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_req: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        if isinstance(exc, RateLimited) and isinstance(exc.details, dict) and "retry_after" in exc.details:
            headers = {"Retry-After": str(exc.details["retry_after"])}
        return _envelope(exc.status_code, exc.code, exc.message, exc.details, headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return _envelope(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed", 401: "unauthorized", 403: "forbidden"}.get(
            exc.status_code, "http_error"
        )
        return _envelope(exc.status_code, code, str(exc.detail), None, getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", exc_info=exc)
        return _envelope(500, "internal_error", "An internal error occurred. Quote the request_id when reporting it.")
