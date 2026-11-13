"""Redis-backed rate limiting and login lockout.

Counters live in Redis so limits hold across every API instance. If Redis is unreachable the limiter
fails *closed* for authentication endpoints in production (to keep brute-force protection) and
*open* for general API traffic (to keep the product usable); both cases are logged.
"""

from __future__ import annotations

import logging
import time

import redis

from app.core.config import get_settings
from app.core.errors import RateLimited, Unavailable
from app.core.redis_client import get_redis

logger = logging.getLogger("aurora.ratelimit")


def hit(key: str, limit: int, window_s: int, *, fail_closed: bool = False) -> None:
    """Sliding-window-ish limiter using two fixed windows weighted by elapsed time."""
    now = time.time()
    window = int(now // window_s)
    elapsed_frac = (now % window_s) / window_s
    cur_key, prev_key = f"rl:{key}:{window}", f"rl:{key}:{window - 1}"
    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.incr(cur_key)
        pipe.expire(cur_key, window_s * 2)
        pipe.get(prev_key)
        cur, _, prev = pipe.execute()
    except redis.RedisError as exc:
        logger.warning("rate limiter unavailable: %s", exc)
        if fail_closed and get_settings().is_production:
            raise Unavailable("Rate limiter unavailable; try again shortly") from exc
        return
    estimated = int(cur) + int(prev or 0) * (1 - elapsed_frac)
    if estimated > limit:
        raise RateLimited("Too many requests", details={"retry_after": window_s - int(now % window_s)})
