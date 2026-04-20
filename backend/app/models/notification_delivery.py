"""SQLAlchemy model for notification_delivery.

One row per dispatch attempt at the subscription level — not per HTTP retry.
For email channels, retry-level accounting stays in `email_delivery` (owned
by the EmailService) and `email_delivery_id` points at that entry. For
webhook channels, this repo owns the retry loop and `attempt_count` +
`last_error` on this row are authoritative. See D3 on SFBL-117.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.email_delivery import EmailDelivery
    from app.models.load_run import LoadRun
    from app.models.notification_subscription import NotificationSubscription


class NotificationDeliveryStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    failed = "failed"


class NotificationDelivery(Base):
    __tablename__ = "notification_delivery"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    subscription_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("notification_subscription.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL for /test dispatches, which have no associated run.
    run_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("load_run.id", ondelete="SET NULL"), nullable=True
    )
    is_test: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    status: Mapped[NotificationDeliveryStatus] = mapped_column(
        SAEnum(NotificationDeliveryStatus, name="notification_delivery_status_enum"),
        nullable=False,
        default=NotificationDeliveryStatus.pending,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_delivery_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("email_delivery.id", ondelete="SET NULL"),
        nullable=True,
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now()
    )

    subscription: Mapped["NotificationSubscription"] = relationship(
        "NotificationSubscription", back_populates="deliveries"
    )
    run: Mapped[Optional["LoadRun"]] = relationship("LoadRun")
    email_delivery: Mapped[Optional["EmailDelivery"]] = relationship("EmailDelivery")

    __table_args__ = (
        Index(
            "ix_notification_delivery_subscription_run",
            "subscription_id",
            "run_id",
        ),
    )
