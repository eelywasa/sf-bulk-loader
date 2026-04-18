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
