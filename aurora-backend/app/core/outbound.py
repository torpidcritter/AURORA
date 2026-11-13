"""Outbound HTTP to addresses that users type in (digest webhooks).

A user-supplied URL must not let the server reach internal services (SSRF). Every URL is checked before it is saved
and again before each send: HTTPS only, no credentials in the URL, default port, and every address the host resolves
to must be public. Redirects are not followed. A host whose DNS answer changes between the check and the connection
(DNS rebinding) is a residual risk; the egress firewall should also block private ranges in production.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

from app.core.config import get_settings
from app.core.errors import ValidationFailed

TIMEOUT_S = 10.0


def _resolve(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})
    except socket.gaierror as exc:
        raise ValidationFailed(f"{host} could not be resolved") from exc


def check_url(url: str, *, allowed_hosts: tuple[str, ...] | None = None) -> str:
    """Return the URL if it is safe to call, otherwise raise ValidationFailed with a plain reason."""
    allow_private = get_settings().outbound_allow_private
    parts = urlsplit(url.strip())
    if parts.scheme != "https" and not (allow_private and parts.scheme == "http"):
        raise ValidationFailed("The address must start with https://")
    if parts.username or parts.password:
        raise ValidationFailed("The address must not contain a user name or password")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValidationFailed("The address has no host")
    if parts.port not in (None, 443) and not allow_private:
        raise ValidationFailed("Only the standard HTTPS port is allowed")
    if allowed_hosts is not None and host not in allowed_hosts:
        raise ValidationFailed(f"The address must be on {', '.join(allowed_hosts)}")
    if not allow_private:
        for addr in _resolve(host):
            ip = ipaddress.ip_address(addr)
            if not ip.is_global or ip.is_multicast:
                raise ValidationFailed("The address points to a private or reserved network")
    return url.strip()


def post_json(url: str, payload: dict, *, client: httpx.Client | None = None, headers: dict | None = None) -> None:
    check_url(url)
    c = client or httpx.Client(timeout=TIMEOUT_S, follow_redirects=False)
    try:
        r = c.post(url, json=payload, headers=headers or {})
        if r.is_redirect:
            raise RuntimeError(f"Receiver answered with a redirect ({r.status_code}); redirects are not followed")
        r.raise_for_status()
    finally:
        if client is None:
            c.close()
