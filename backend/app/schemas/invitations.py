"""Pydantic schemas for the public invitation-accept API (SFBL-202)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class InvitationInfoResponse(BaseModel):
    """Returned by GET /api/invitations/{token} — info for the welcome screen."""

    email: str
    display_name: Optional[str] = None
    profile_name: Optional[str] = None


class InvitationAcceptRequest(BaseModel):
    """Body for POST /api/invitations/{token}/accept."""

    password: str


class InvitationAcceptResponse(BaseModel):
    """Response for POST /api/invitations/{token}/accept — the user is now logged in."""

    access_token: str
    token_type: str = "bearer"
