"""SQLAlchemy model for the user_backup_code table (SFBL-245).

One row per minted backup code. A set of 10 codes (spec §0 D9) is minted at
TOTP enrolment confirmation and rotated atomically on regenerate: the old
set is deleted and a new set inserted in a single transaction. Each code is
stored only as a bcrypt hash; the plaintext is shown to the user once and
never persisted.

See ``docs/specs/2fa-totp.md`` §3.2.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserBackupCode(Base):
    """A single backup code for a user's 2FA recovery set."""

    __tablename__ = "user_backup_code"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )

    # bcrypt hash of the plaintext code (cost 12). String(60) is bcrypt's
    # fixed output width.
    code_hash: Mapped[str] = mapped_column(String(60), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # NULL until the code is redeemed. Set atomically on consume so two
    # simultaneous redemptions of the same code race safely.
    consumed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Captured on consume for audit (String(45) fits IPv4-mapped IPv6 form).
    consumed_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)

    __table_args__ = (
        # Supports the hot-path query "how many unconsumed codes remain for
        # this user?" (for exhaustion warnings and the /me payload).
        Index("ix_user_backup_code_user_consumed", "user_id", "consumed_at"),
    )
