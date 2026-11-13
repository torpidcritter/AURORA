"""Shared Redis client (used for distributed rate limiting and readiness checks)."""

from __future__ import annotations

from functools import lru_cache

import redis

from app.core.config import get_settings

_override: redis.Redis | None = None


def set_redis_override(client: redis.Redis | None) -> None:
    """Tests inject fakeredis here."""
    global _override
    _override = client


@lru_cache
def _client() -> redis.Redis:
    return redis.Redis.from_url(
        get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2, health_check_interval=30
    )


def get_redis() -> redis.Redis:
    return _override if _override is not None else _client()
