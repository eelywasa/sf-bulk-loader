from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class MfaStatus(BaseModel):
    """2FA enrolment status for the current user (SFBL-246 / SFBL-244).

    Returned as the ``mfa`` sub-object on ``/api/auth/me``. When the user has
    no ``user_totp`` row, ``enrolled`` is false, ``enrolled_at`` is null, and
    ``backup_codes_remaining`` is 0.
    """

    enrolled: bool
    enrolled_at: Optional[datetime] = None
    backup_codes_remaining: int = 0


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    # SFBL-190: must_reset_password flag — set when credentials are valid but the
    # user's must_reset_password flag is True.  The JWT is still issued so the
    # frontend can authenticate the password-change API, but clients MUST redirect
    # to the reset flow before granting normal access.  Frontend wiring: SFBL-202.
    must_reset_password: bool = False
    # SFBL-248: explicit false on the full-token branch of the login response
    # union — lets the frontend discriminate without peeking at keys.
    mfa_required: bool = False


class MfaRequiredResponse(BaseModel):
    """Phase-1 response when login requires a second factor (SFBL-248).

    The caller exchanges ``mfa_token`` for a full ``TokenResponse`` via
    ``POST /api/auth/login/2fa`` (when already enrolled) or
    ``POST /api/auth/login/2fa/enroll-and-verify`` (when ``must_enroll``
    is true).
    """

    mfa_required: bool = True
    mfa_token: str
    mfa_methods: List[str]
    must_enroll: bool


class ProfileSummary(BaseModel):
    """Minimal profile shape included in the /me response."""

    name: str


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: Optional[str]
    # status replaces is_active as the canonical state field (SFBL-189).
    # is_active is kept as a read-only derived field for API compatibility —
    # computed by the User.is_active property on the ORM model.
    status: str
    is_active: bool
    # SFBL-195: profile and permissions for frontend permission checks.
    # profile.name is "admin" | "operator" | "viewer" | "desktop" (desktop mode).
    # permissions is a sorted list of permission keys held by the user.
    profile: Optional[ProfileSummary] = None
    permissions: List[str] = []
    # SFBL-246: 2FA status. Defaults to "not enrolled" so the field is always
    # present in the response even before the enrolment API lands.
    mfa: MfaStatus = MfaStatus(enrolled=False, enrolled_at=None, backup_codes_remaining=0)

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
