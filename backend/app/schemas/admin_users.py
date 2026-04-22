"""Pydantic schemas for the admin user-management API (SFBL-200).

All schemas in this module are used exclusively by ``/api/admin/users/*``
endpoints and are separate from the per-user ``/api/me`` schemas in
``app/schemas/auth.py``.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr


# ── Response schemas ──────────────────────────────────────────────────────────


class AdminProfileSummary(BaseModel):
    """Minimal profile shape embedded in admin user responses."""

    id: str
    name: str

    model_config = ConfigDict(from_attributes=True)


class AdminUserResponse(BaseModel):
    """Full user detail returned by admin endpoints."""

    id: str
    email: str
    display_name: Optional[str]
    status: str
    is_admin: bool
    profile: Optional[AdminProfileSummary] = None
    permissions: List[str] = []
    invited_by: Optional[str] = None
    invited_at: Optional[str] = None   # ISO-8601 string; None for bootstrap admin
    last_login_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_user(cls, user: object) -> "AdminUserResponse":
        """Build an AdminUserResponse from an ORM User instance."""
        from app.models.user import User as _User

        u: _User = user  # type: ignore[assignment]
        profile_summary: Optional[AdminProfileSummary] = None
        permissions: List[str] = []
        if u.profile is not None:
            profile_summary = AdminProfileSummary(id=u.profile.id, name=u.profile.name)
            permissions = sorted(u.profile.permission_keys)

        return cls(
            id=u.id,
            email=u.email,
            display_name=u.display_name,
            status=u.status,
            is_admin=u.is_admin,
            profile=profile_summary,
            permissions=permissions,
            invited_by=u.invited_by,
            invited_at=u.invited_at.isoformat() if u.invited_at else None,
            last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
        )


class AdminUserListResponse(BaseModel):
    """Paginated list response for GET /api/admin/users."""

    items: List[AdminUserResponse]
    total: int
    page: int
    page_size: int


# ── Request schemas ───────────────────────────────────────────────────────────


class InviteUserRequest(BaseModel):
    """Body for POST /api/admin/users (invite flow)."""

    email: EmailStr
    profile_id: str
    display_name: Optional[str] = None


class UpdateUserRequest(BaseModel):
    """Body for PUT /api/admin/users/{id}."""

    profile_id: Optional[str] = None
    display_name: Optional[str] = None


# ── Invite response ───────────────────────────────────────────────────────────


class InviteUserResponse(BaseModel):
    """Response for POST /api/admin/users.

    ``raw_token`` is included so the caller (SFBL-202 email flow) can
    construct the accept URL.  It is returned exactly once and never stored.
    """

    user: AdminUserResponse
    raw_token: str
    expires_at: str    # ISO-8601


# ── Reset-password response ───────────────────────────────────────────────────


class AdminResetPasswordResponse(BaseModel):
    """Response for POST /api/admin/users/{id}/reset-password.

    Always returns a temporary password.  The user is required to change it
    on next login (``must_reset_password=True``).  SFBL-202 will wire the
    email delivery path so the temp password is not transmitted in-band.
    For now, the raw temp password is returned in this response only.
    """

    temp_password: str
    must_reset_password: bool = True


# ── Resend-invite response ────────────────────────────────────────────────────


class ResendInviteResponse(BaseModel):
    """Response for POST /api/admin/users/{id}/resend-invite."""

    raw_token: str
    expires_at: str    # ISO-8601
