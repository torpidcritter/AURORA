"""Request dependencies: authenticated principal and permission checks (server-side RBAC)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, Unauthorized
from app.core.security import Perm, decode_access_token, has_perm
from app.db.models import User
from app.db.session import get_db

_bearer = HTTPBearer(auto_error=False)


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
    # Only trust X-Forwarded-For when uvicorn is started with --proxy-headers behind a known proxy;
    # request.client already reflects that configuration.
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
    return Principal(user_id=user.id, org_id=user.org_id, role=user.role, email=user.email, ip=client_ip(request))


def require(perm: Perm) -> Callable[[Principal], Principal]:
    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.can(perm):
            raise Forbidden(f"Your role ({principal.role}) does not allow this action")
        return principal

    return _dep
