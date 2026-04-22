import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.profile import Profile

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
    # Local auth — email is the unique login identifier (SFBL-198: username dropped)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Identity
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
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
    # Admin flag — set TRUE for users with administrative privileges (backfilled in 0019).
    # Still present until SFBL-195 completes the permission-guard migration.
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.false())
    # Profile FK — replaces role column (migrated in 0022). lazy=joined so auth
    # middleware gets profile + permissions in a single query.
    profile_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("profiles.id"), nullable=True
    )
    profile: Mapped[Optional["Profile"]] = relationship(
        "Profile", foreign_keys=[profile_id], lazy="selectin"
    )
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

    # ── User lifecycle / invitation columns (SFBL-199) ────────────────────────
    # Who invited this user.  NULL for the bootstrap admin account (no inviter).
    # Self-referential FK — the inviting user's row.  SET NULL on inviter delete
    # so that orphaned invitees are not deleted along with the inviter.
    invited_by: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    # When the invitation was issued.  NULL for bootstrap admin.
    invited_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # Updated to now() on every successful authentication.  NULL until the user
    # logs in for the first time.  Used for activity reporting and idle-session
    # cleanup (SFBL-200 and beyond).
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
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
