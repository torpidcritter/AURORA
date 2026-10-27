"""FastAPI application factory."""

from __future__ import annotations

import importlib
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware

ROUTERS = [
    ("app.api.routes.health", "router"),
    ("app.api.routes.auth", "router"),
    ("app.api.routes.auth", "users_router"),
    ("app.api.routes.data", "router"),
    ("app.api.routes.forecasting", "router"),
    ("app.api.routes.ops", "router"),
    ("app.api.routes.assistant", "router"),
    ("app.api.routes.shopify", "router"),
    ("app.api.routes.settings", "router"),
    ("app.api.routes.purchasing", "router"),
    ("app.api.routes.locations", "router"),
    ("app.api.routes.digests", "router"),
    ("app.api.routes.data_rights", "router"),
]


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    app = FastAPI(
        title="Aurora API",
        version="0.1.0",
        description="Demand forecasting and inventory intelligence API.",
        docs_url="/docs",
        redoc_url=None,
    )
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)
    for module, attr in ROUTERS:
        app.include_router(getattr(importlib.import_module(module), attr))
    logging.getLogger("aurora").info("app created", extra={"extra_fields": {"environment": s.environment}})
    return app


app = create_app()
