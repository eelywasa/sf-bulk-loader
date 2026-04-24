"""SQLAlchemy model for the user_totp table (SFBL-245).

One row per enrolled user. A row's existence is the authoritative signal that
the user has TOTP 2FA configured; per spec §0 D11, no row is written until
the user successfully confirms their first code during enrolment. The shared
TOTP secret is Fernet-encrypted at rest using the existing ``ENCRYPTION_KEY``.

See ``docs/specs/2fa-totp.md`` §3.1 for the authoritative schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserTotp(Base):
    """A user's confirmed TOTP factor."""

    __tablename__ = "user_totp"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # One factor per user — unique enforces that.
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Fernet-encrypted base32 TOTP secret. Encryption key = ``ENCRYPTION_KEY``
    # env var (same key that protects Salesforce private keys).
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # RFC 6238 parameters — defaulted per spec §0 D10 for maximum authenticator
    # compatibility. Columns exist for forward compatibility if an operator
    # ever needs per-user overrides.
    algorithm: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="SHA1"
    )
    digits: Mapped[int] = mapped_column(Integer, nullable=False, server_default="6")
    period_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="30"
    )

    # Row exists ⇒ user is enrolled. ``enrolled_at`` is the confirmation
    # timestamp (stateless enrolment means no "pending" state persists).
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Anti-replay: the counter (= floor(unix_time / period_seconds)) of the
    # last successful code. NULL until first successful redemption. A replay
    # of the same code within its 30-second window is rejected by comparing
    # the presented counter against this value.
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_counter: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
