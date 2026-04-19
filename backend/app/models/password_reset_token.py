"""SQLAlchemy model for the password_reset_token table.

Each row represents a single-use password-reset link token.
The raw token is never stored — only its SHA-256 hex digest is persisted.

Token lifecycle:
  1. A raw token is generated and e-mailed to the user.
  2. When the user clicks the link the raw token is hashed and looked up.
  3. ``used_at`` is set atomically on redemption; subsequent attempts are rejected.
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


class PasswordResetToken(Base):
    __tablename__ = "password_reset_token"

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

    # Audit / abuse detection — 45 chars accommodates full IPv6 addresses
    request_ip: Mapped[Optional[str]] = mapped_column(
        String(45), nullable=True
    )

    __table_args__ = (
        # Typical sweep: find unexpired, unused tokens by hash
        Index("ix_password_reset_token_expires_used", "expires_at", "used_at"),
    )
