"""SQLAlchemy model for the invitation_tokens table.

Each row represents a single-use invitation link sent to a prospective user.
The raw token is never stored — only its SHA-256 hex digest is persisted.

Token lifecycle
---------------
1. An admin calls the invite API; a raw token is generated and emailed to the
   invitee along with a sign-up URL.
2. When the invitee clicks the link the raw token is hashed and looked up by
   ``token_hash``.
3. Status is **derived** from the timestamps — never stored as a column:
   - *pending*  — ``used_at IS NULL AND expires_at > now()``
   - *used*     — ``used_at IS NOT NULL``
   - *expired*  — ``used_at IS NULL AND expires_at <= now()``
4. Redemption uses an atomic UPDATE guarded by ``used_at IS NULL AND
   expires_at > now()`` so that two simultaneous redeems race safely — only
   the first UPDATE that matches the WHERE clause wins.  The application MUST
   check the affected-row count and reject the second attempt.

Example atomic redeem (SQLAlchemy core)::

    result = await session.execute(
        update(InvitationToken)
        .where(
            InvitationToken.token_hash == hashed,
            InvitationToken.used_at.is_(None),
            InvitationToken.expires_at > func.now(),
        )
        .values(used_at=func.now())
        .returning(InvitationToken.id, InvitationToken.user_id)
    )
    row = result.first()
    if row is None:
        raise InvalidOrExpiredToken()

See docs/specs/ for §4.4, §6.1, §6.2, §6.3 — invite flow authoritative spec.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InvitationToken(Base):
    """Single-use invitation token.

    The raw token is never persisted; only the SHA-256 hex digest in
    ``token_hash`` is stored.  Status is derived from timestamps — see module
    docstring for the derivation rules and the atomic redeem pattern.
    """

    __tablename__ = "invitation_tokens"

    # Primary key — UUID string, consistent with other models
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # The user account that this invitation is for.  Set when the invite is
    # accepted and a User row is created; NULL until then if the invitation is
    # pre-provisioned without a user row.  Or pre-set to a stub 'invited' user.
    # FK → user.id; CASCADE so orphan tokens are swept when a user is deleted.
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # SHA-256 hex of the raw token (64 hex chars); raw token is NEVER stored.
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    # Validity window — TTL computed by the application from INVITATION_TTL_HOURS.
    # The DB stores the absolute expiry so polling and DB-level expiry checks are
    # straightforward.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Redemption timestamp — set atomically on first use; NULL = not yet used.
    # Once set the token is permanently invalid regardless of expiry.
    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # Fast lookup of pending (unredeemed, unexpired) tokens by hash —
        # the primary access pattern during invitation acceptance.
        Index("ix_invitation_tokens_expires_used", "expires_at", "used_at"),
    )
