"""SQLAlchemy model for the email_change_token table.

Each row represents a pending email-address change request.
The raw token is never stored — only its SHA-256 hex digest is persisted.

Token lifecycle:
  1. User requests an address change; a raw token is generated and sent to
     ``new_email`` for verification.
  2. When the user clicks the link the raw token is hashed and looked up.
  3. ``used_at`` is set atomically on redemption; subsequent attempts are
     rejected.
  4. Rows with ``expires_at`` in the past are treated as invalid regardless of
     ``used_at``.

See docs/specs/user-profile-reset-spec.md for the authoritative column
definitions.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EmailChangeToken(Base):
    __tablename__ = "email_change_token"

    # Primary key — UUID string, consistent with other models in this project
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Owner — indexed for fast per-user lookups; CASCADE on user deletion
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # SHA-256 hex of the raw token; raw token is NEVER stored
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    # Target address — the verification email is sent here
    new_email: Mapped[str] = mapped_column(String(320), nullable=False)

    # Validity window
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Redemption timestamp — set on first use; NULL = not yet redeemed
    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        # Typical sweep: find unexpired, unused tokens by hash
        Index("ix_email_change_token_expires_used", "expires_at", "used_at"),
    )
