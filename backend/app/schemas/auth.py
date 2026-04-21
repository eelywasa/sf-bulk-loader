from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: str
    username: Optional[str]
    email: Optional[str]
    display_name: Optional[str]
    role: str
    # status replaces is_active as the canonical state field (SFBL-189).
    # is_active is kept as a read-only derived field for API compatibility —
    # computed by the User.is_active property on the ORM model.
    status: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class AuthConfigResponse(BaseModel):
    saml_enabled: bool


# ─── SFBL-146: password change ─────────────────────────────────────────────
class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


# ─── SFBL-148: profile + email change ──────────────────────────────────────
class ProfileUpdateRequest(BaseModel):
    display_name: Optional[str] = None


class EmailChangeRequest(BaseModel):
    new_email: EmailStr


class EmailChangeConfirm(BaseModel):
    token: str


# ─── SFBL-147: password reset ──────────────────────────────────────────────
class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str
