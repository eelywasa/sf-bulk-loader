"""SQLAlchemy model for the email_delivery table.

Each row records one send attempt seam — one recipient, one backend call.
Columns are designed to support:
  - CAS-based retry claims (claimed_by, claim_expires_at, next_attempt_at)
  - Delivery log privacy (to_hash / to_domain by default; to_addr opt-in)
  - Idempotent sends (idempotency_key, unique when present)

See docs/specs/implemented/email-service-spec.md § Data model for the authoritative
column definitions.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DeliveryStatus(str, enum.Enum):
    pending = "pending"
    sending = "sending"
    sent = "sent"
    failed = "failed"
    skipped = "skipped"


class EmailDelivery(Base):
    __tablename__ = "email_delivery"

    # Primary key — UUID string, consistent with other models in this project
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Audit timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Message metadata
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    template: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    backend: Mapped[str] = mapped_column(String(20), nullable=False)

    # Recipient — privacy model: hash + domain by default, plaintext opt-in
    to_hash: Mapped[str] = mapped_column(String(64), nullable=False)   # sha256 hex
    to_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    to_addr: Mapped[Optional[str]] = mapped_column(
        String(320), nullable=True
    )  # populated only if EMAIL_LOG_RECIPIENTS=true

    # Rendered subject (validated before insert; bodies are NOT stored)
    subject: Mapped[str] = mapped_column(Text, nullable=False)

    # Lifecycle
    status: Mapped[DeliveryStatus] = mapped_column(
        SAEnum(DeliveryStatus, name="delivery_status_enum"),
        nullable=False,
        default=DeliveryStatus.pending,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Snapshot of email_max_retries + 1 at insert time so per-row budgets
    # are stable even if the operator changes EMAIL_MAX_RETRIES mid-flight.
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)

    # Error tracking
    last_error_code: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # normalised EmailErrorReason value — never a raw provider code
    last_error_msg: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # sanitised via safe_exc_message; may prefix raw provider code

    # Provider result
    provider_message_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    # Idempotency — unique when present; NULL values are distinct per SQL standard
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, unique=True
    )

    # CAS claim lease
    claimed_by: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )  # "{hostname}:{pid}"
    claim_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Retry scheduling
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Terminal timestamp
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # For retry sweep queries
        Index("ix_email_delivery_status_next_attempt", "status", "next_attempt_at"),
        # For boot-sweep reaping
        Index("ix_email_delivery_status_claim_expires", "status", "claim_expires_at"),
    )
