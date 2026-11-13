"""Time-based one-time passwords (RFC 6238, HMAC-SHA1, 6 digits, 30-second steps) as used by authenticator apps.

Implemented with the standard library so no extra dependency handles secrets. Checked against the RFC 6238 test
vectors in tests/unit/test_totp.py.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1  # accept the previous and next step for clock drift


def new_secret() -> str:
    """A 160-bit secret, base32 without padding (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret: str, step: int, digits: int = DIGITS, digest: str = "sha1") -> str:
    mac = hmac.new(_key(secret), struct.pack(">Q", step), getattr(hashlib, digest)).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def verify(secret: str, code: str, *, last_step: int | None = None, now: float | None = None) -> int | None:
    """The matched time step, or None. A step at or before ``last_step`` is refused so a code works only once."""
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in range(step - WINDOW, step + WINDOW + 1):
        if last_step is not None and candidate <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: str, account: str, issuer: str = "Aurora") -> str:
    label = quote(f"{issuer}:{account}", safe="@:")
    query = urlencode(
        {"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS, "period": STEP_SECONDS}
    )
    return f"otpauth://totp/{label}?{query}"
