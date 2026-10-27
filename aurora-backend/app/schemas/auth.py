from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field

from app.schemas.common import ORMModel

RoleName = Literal["viewer", "operator", "analyst", "admin"]


class LoginRequest(BaseModel):
    """The username is the account's email address. `username` is accepted as an alias for clients that follow the
    spec's username/password wording or the OAuth2 password-grant field names."""

    model_config = ConfigDict(populate_by_name=True)

    email: str = Field(min_length=3, max_length=320, validation_alias=AliasChoices("email", "username"))
    password: str = Field(min_length=1, max_length=256)
    otp: str | None = Field(default=None, max_length=20, description="Authenticator code or a recovery code")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    refresh_expires_in: int


class UserOut(ORMModel):
    id: uuid.UUID
    org_id: uuid.UUID
    email: str
    full_name: str
    role: RoleName
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
    two_factor_enabled: bool = False


class MeOut(UserOut):
    permissions: list[str]
    org_name: str
    two_factor_required: bool = False  # the organisation requires it and this account has not set it up


class PasswordConfirm(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)


class TwoFactorCode(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class TwoFactorDisable(PasswordConfirm, TwoFactorCode):
    pass


class TwoFactorPolicy(BaseModel):
    required: bool


class RegisterRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(default="", max_length=200)
    password: str = Field(min_length=10, max_length=256)
    role: RoleName = "viewer"


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    role: RoleName | None = None
    is_active: bool | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=10, max_length=256)


class ResetLinkOut(BaseModel):
    reset_url: str
    expires_at: datetime


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=10, max_length=256)
