"""Pydantic schemas for the PAT management API (SFBL-368).

Three shapes:
- PatCreate       — request body for POST /api/me/tokens
- PatCreateResponse — response for POST (includes the plaintext token, returned ONCE)
- PatMetadata     — list / read response (never includes plaintext or hash)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class PatCreate(BaseModel):
    """Request body for issuing a new PAT."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable label for this token (e.g. 'CI pipeline').",
    )
    expires_at: Optional[datetime] = Field(
        None,
        description=(
            "Optional absolute expiry timestamp (timezone-aware ISO 8601). "
            "Omit or pass null for a non-expiring token."
        ),
    )

    @field_validator("expires_at")
    @classmethod
    def _normalise_expiry_tz(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Treat a naive expiry as UTC.

        A naive datetime (e.g. ``2026-06-02T12:00:00`` with no offset) would
        otherwise reach ``pat_service.issue`` and raise a ``ValueError`` that
        surfaces as a 500 for ordinary bad input. Normalising to UTC keeps the
        value usable and the auth-path comparison timezone-aware.
        """
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class PatMetadata(BaseModel):
    """Safe metadata for a PAT — never exposes the plaintext or hash."""

    id: str
    name: str
    prefix: str
    last4: str
    created_at: datetime
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]

    model_config = {"from_attributes": True}


class PatCreateResponse(PatMetadata):
    """Response for POST /api/me/tokens.

    Extends PatMetadata with the **plaintext token**, which is returned ONCE
    and cannot be recovered after this response is sent.
    """

    token: str = Field(
        ...,
        description=(
            "The full plaintext token string.  Store it securely — it will NOT "
            "be shown again."
        ),
    )
