"""Encryption at rest for third-party credentials (e.g. Shopify access tokens).

Uses Fernet (AES-128-CBC + HMAC-SHA256). `TOKEN_ENCRYPTION_KEYS` holds one or more keys, newest first:
new values are encrypted with the first key and any listed key can decrypt, so keys can be rotated without
downtime (add the new key in front, re-encrypt with `rotate`, then drop the old key).
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings

log = logging.getLogger(__name__)


def _fernet() -> MultiFernet:
    s = get_settings()
    if s.token_encryption_keys is not None:
        keys = [k.strip() for k in s.token_encryption_keys.get_secret_value().split(",") if k.strip()]
        return MultiFernet([Fernet(k) for k in keys])
    if s.is_production:  # also enforced by the settings validator
        raise RuntimeError("TOKEN_ENCRYPTION_KEYS is not configured")
    # Development only: a key derived from SECRET_KEY, so local installs work without extra setup.
    derived = base64.urlsafe_b64encode(
        hashlib.sha256(b"aurora-dev-token-key:" + s.secret_key.get_secret_value().encode()).digest()
    )
    return MultiFernet([Fernet(derived)])


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored credential cannot be decrypted with the configured keys") from exc


def rotate(token: str) -> str:
    """Re-encrypt with the newest key."""
    try:
        return _fernet().rotate(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored credential cannot be decrypted with the configured keys") from exc
