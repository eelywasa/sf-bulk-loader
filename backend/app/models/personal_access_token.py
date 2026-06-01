"""SQLAlchemy model for the personal_access_token table (SFBL-366).

Each row represents a long-lived Personal Access Token (PAT) issued to a user.

Token lifecycle:
  1. The service generates a high-entropy secret with a recognisable prefix.
  2. A keyed HMAC-SHA256 digest of the secret is persisted; the plaintext is
     returned ONCE to the caller and is never stored.
  3. Subsequent requests derive the same hash from the bearer value and look it
     up via the unique index — O(1) indexed lookup, then a constant-time compare.
  4. ``revoked_at`` is set on revocation; revoked tokens are rejected by SFBL-367.
  5. Optional ``expires_at`` — NULL means the token never expires.

``scope`` column
-----------------
Present for forward-compatibility with a future fine-grained permission model.
In v1 this column is NOT enforced — a PAT grants the same access as a normal
session token for the owning user.  Do not enforce or validate scope in v1 code
paths; the column exists so the schema is stable when scoped-PAT enforcement
ships (SFBL-TODO).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PersonalAccessToken(Base):
    __tablename__ = "personal_access_token"

    # Primary key — UUID string, consistent with other models
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Owner — CASCADE on user deletion; indexed for per-user list queries
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Human-readable name given by the user at issuance (e.g. "CI pipeline")
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # HMAC-SHA256 hex digest of the plaintext token (keyed with a server-side
    # secret derived from ENCRYPTION_KEY). The unique index enables O(1) lookup.
    # Raw token is NEVER stored — only this digest.
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    # Fixed recognisable prefix that lets users identify which token they are
    # looking at (e.g. "sfbl_pat_"). Stored so it can be echoed back in list
    # responses without touching the plaintext secret.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)

    # Last 4 characters of the full token — safe to display for identification
    last4: Mapped[str] = mapped_column(String(4), nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    # Updated each time the token is successfully used to authenticate.
    # NULL until first use. Used for activity reporting.
    # NOTE: This column is NOT updated in this ticket (SFBL-366).
    # The update-on-use logic belongs in SFBL-367 (auth middleware).
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # NULL means the token never expires. When set, SFBL-367 rejects tokens
    # whose expires_at is in the past.
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Set when the token is explicitly revoked; NULL means active.
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Forward-compat scope field. NOT enforced in v1. See module docstring.
    # Stored as text (JSON-serialisable string) for portability across
    # SQLite and Postgres without requiring a native JSON column.
    scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationship back to the owning user — lazy="selectin" consistent with
    # how Profile is loaded on User (see user.py).
    # Import guard avoids circular model imports.
    user: Mapped[Optional["app.models.user.User"]] = relationship(  # type: ignore[name-defined]
        "User",
        foreign_keys=[user_id],
        lazy="selectin",
    )

    __table_args__ = (
        # Composite index supporting the SFBL-368 "list tokens for a user,
        # ordered by created_at" query pattern.
        Index("ix_pat_user_created", "user_id", "created_at"),
    )
