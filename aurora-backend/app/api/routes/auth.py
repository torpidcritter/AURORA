from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Principal, client_ip, get_principal, require
from app.core.errors import Forbidden
from app.core.security import ROLE_PERMISSIONS, Perm, Role
from app.db.models import Organization, User
from app.db.session import get_db
from app.schemas.auth import (
    LoginRequest,
    MeOut,
    PasswordChange,
    PasswordReset,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
    UserUpdate,
)
from app.schemas.common import Page
from app.services import audit, auth_service

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


def _pair(t: auth_service.IssuedTokens) -> TokenPair:
    return TokenPair(
        access_token=t.access_token,
        refresh_token=t.refresh_token,
        expires_in=t.expires_in,
        refresh_expires_in=t.refresh_expires_in,
    )


@router.post("/login", response_model=TokenPair)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenPair:
    return _pair(
        auth_service.login(db, body.email, body.password, client_ip(request), request.headers.get("user-agent"))
    )


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, request: Request, db: Session = Depends(get_db)) -> TokenPair:
    return _pair(auth_service.refresh(db, body.refresh_token, client_ip(request), request.headers.get("user-agent")))


@router.post("/logout", status_code=204)
def logout(body: RefreshRequest, request: Request, db: Session = Depends(get_db)) -> None:
    auth_service.logout(db, body.refresh_token, client_ip(request))


@router.get("/me", response_model=MeOut)
def me(p: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> MeOut:
    user = db.get(User, p.user_id)
    org = db.get(Organization, p.org_id)
    assert user is not None and org is not None
    return MeOut(
        **UserOut.model_validate(user).model_dump(),
        permissions=sorted(ROLE_PERMISSIONS[Role(user.role)]),
        org_name=org.name,
    )


@router.post("/register", response_model=UserOut, status_code=201)
def register(
    body: RegisterRequest, p: Principal = Depends(require(Perm.manage_users)), db: Session = Depends(get_db)
) -> User:
    user = auth_service.create_user(
        db, org_id=p.org_id, email=body.email, password=body.password, role=body.role, full_name=body.full_name
    )
    audit.record_for(
        db,
        p,
        "user.create",
        resource_type="user",
        resource_id=user.id,
        details={"email": user.email, "role": user.role},
    )
    db.commit()
    return user


@router.post("/password", status_code=204)
def change_password(body: PasswordChange, p: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> None:
    user = db.get(User, p.user_id)
    assert user is not None
    auth_service.change_password(db, user, body.current_password, body.new_password)
    audit.record_for(db, p, "user.password_change", resource_type="user", resource_id=user.id)
    db.commit()


@users_router.get("", response_model=Page[UserOut])
def list_users(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require(Perm.manage_users)),
    db: Session = Depends(get_db),
) -> Page[UserOut]:
    q = select(User).where(User.org_id == p.org_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(User.created_at).limit(limit).offset(offset)).all()
    return Page(items=[UserOut.model_validate(u) for u in rows], total=total, limit=limit, offset=offset)


@users_router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    p: Principal = Depends(require(Perm.manage_users)),
    db: Session = Depends(get_db),
) -> User:
    user = auth_service.get_org_user(db, p.org_id, user_id)
    changes = body.model_dump(exclude_unset=True)
    if user.id == p.user_id and (changes.get("role", user.role) != "admin" or changes.get("is_active") is False):
        raise Forbidden("You cannot demote or deactivate your own account")
    if "role" in changes and changes["role"] != user.role or changes.get("is_active") is False:
        user.token_version += 1
        auth_service.revoke_all_for_user(db, user.id, "role_or_status_change")
    for k, v in changes.items():
        setattr(user, k, v)
    audit.record_for(db, p, "user.update", resource_type="user", resource_id=user.id, details=changes)
    db.commit()
    return user


@users_router.post("/{user_id}/password", status_code=204)
def reset_password(
    user_id: uuid.UUID,
    body: PasswordReset,
    p: Principal = Depends(require(Perm.manage_users)),
    db: Session = Depends(get_db),
) -> None:
    user = auth_service.get_org_user(db, p.org_id, user_id)
    auth_service.set_password(db, user, body.new_password)
    audit.record_for(db, p, "user.password_reset", resource_type="user", resource_id=user.id)
    db.commit()
