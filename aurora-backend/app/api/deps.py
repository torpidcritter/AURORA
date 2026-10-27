"""Request dependencies: authenticated principal and permission checks (server-side RBAC)."""

from __future__ import annotations

import hmac
import ipaddress
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.config import get_settings
from app.core.errors import Forbidden, Unauthorized
from app.core.security import Perm, decode_access_token, has_perm
from app.db.models import User
from app.db.session import get_db

_bearer = HTTPBearer(auto_error=False)
# What an account without two-factor login can still reach when its organisation requires it (ADR-040).
TWO_FACTOR_SETUP_PATHS = frozenset(
    {"/auth/me", "/auth/password", "/auth/two-factor", "/auth/two-factor/setup", "/auth/two-factor/enable"}
)


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str
    email: str
    ip: str | None

    def can(self, perm: Perm) -> bool:
        return has_perm(self.role, perm)


def client_ip(request: Request) -> str | None:
    """The end user's address, used for rate limits and the audit log.

    When the web app's BFF proves itself with BFF_SHARED_SECRET, the address it reports is used: that is how a
    publicly reachable API (Vercel + Railway) learns the real client without trusting X-Forwarded-For from anyone.
    Otherwise request.client, which reflects X-Forwarded-For only when uvicorn trusts the proxy (--proxy-headers with
    FORWARDED_ALLOW_IPS).
    """
    secret = get_settings().bff_shared_secret
    if secret is not None:
        sent = request.headers.get("x-aurora-bff", "")
        reported = request.headers.get("x-aurora-client-ip", "")
        if sent and reported and hmac.compare_digest(sent.encode(), secret.get_secret_value().encode()):
            try:
                return str(ipaddress.ip_address(reported.strip()))
            except ValueError:
                pass
    return request.client.host if request.client else None


def get_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise Unauthorized("Missing bearer token")
    try:
        claims = decode_access_token(creds.credentials)
        user_id = uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise Unauthorized("Invalid or expired token") from exc
    # Re-load the user so deactivation, role changes and password resets take effect immediately.
    user = db.get(User, user_id)
    if user is None or not user.is_active or user.token_version != claims["tv"] or str(user.org_id) != claims["org"]:
        raise Unauthorized("Session is no longer valid")
    # Per-user ceiling on all authenticated traffic (shared across API instances via Redis; fails open if Redis is
    # down). Expensive endpoints have tighter limits of their own.
    rate_limit.hit(f"api:{user.id}", get_settings().api_rate_limit_per_minute, 60)
    if user.totp_enabled_at is None and request.url.path not in TWO_FACTOR_SETUP_PATHS:
        from app.services import two_factor  # local import: services import this module's types

        if two_factor.org_requires(db, user.org_id):
            raise Forbidden(
                "Your organisation requires two-factor login. Set it up under Account security.",
                code="two_factor_required",
            )
    return Principal(user_id=user.id, org_id=user.org_id, role=user.role, email=user.email, ip=client_ip(request))


def require(perm: Perm) -> Callable[[Principal], Principal]:
    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.can(perm):
            raise Forbidden(f"Your role ({principal.role}) does not allow this action")
        return principal

    return _dep
