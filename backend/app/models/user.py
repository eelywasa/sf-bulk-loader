import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Valid status values — kept as a module-level tuple for reuse in schemas/auth.
USER_STATUS_VALUES = ("invited", "active", "locked", "deactivated", "deleted")


class User(Base):
    __tablename__ = "user"

    __table_args__ = (
        CheckConstraint(
            "status IN ('invited', 'active', 'locked', 'deactivated', 'deleted')",
            name="ck_user_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Local auth
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # SAML / profile
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    saml_name_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # User state — replaces is_active (SFBL-189)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # Tier-1 auto-lockout: set to now()+lock_duration on threshold breach; auto-clears at expiry.
    locked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failed_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # Temp-password users must reset on first login.
    must_reset_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Admin flag — set TRUE for users with administrative privileges.
    # Backfilled from role='admin' in migration 0019; Epic B will replace role with profile_id.
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.false())
    # Shared
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )
    # JWT invalidation watermark — any token whose ``iat`` is strictly less than
    # this value is rejected, even if the token's signature and expiry are valid.
    # Set to utcnow() whenever the password or email address is changed.
    # NULL means no watermark (all valid tokens are accepted).
    password_changed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    @property
    def is_active(self) -> bool:
        """Read-only derived property for backward compatibility.

        Returns True only when status is 'active' and the account is not
        subject to a tier-1 lockout (locked_until is None or has expired).

        Note: this does NOT check locked_until against the current time at the
        DB layer; callers that need precise lockout checking should compare
        ``locked_until`` against ``datetime.now(timezone.utc)`` directly.
        The property is kept for schema serialisation compat only.
        """
        from datetime import timezone
        if self.status != "active":
            return False
        if self.locked_until is not None:
            now = datetime.now(timezone.utc)
            lu = self.locked_until
            if lu.tzinfo is None:
                lu = lu.replace(tzinfo=timezone.utc)
            if lu > now:
                return False
        return True
