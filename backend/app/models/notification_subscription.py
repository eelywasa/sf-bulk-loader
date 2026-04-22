"""SQLAlchemy model for notification_subscription.

Records a user's subscription to run-complete notifications for one or all
plans, delivered via one channel (email or webhook) to a single destination.

See docs/specs/implemented/notifications-spec.md and the locked design decisions on
SFBL-117 (D1/D2/D3) for context.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.load_plan import LoadPlan
    from app.models.notification_delivery import NotificationDelivery
    from app.models.user import User


class NotificationChannel(str, enum.Enum):
    email = "email"
    webhook = "webhook"


class NotificationTrigger(str, enum.Enum):
    terminal_any = "terminal_any"
    terminal_fail_only = "terminal_fail_only"


class NotificationSubscription(Base):
    __tablename__ = "notification_subscription"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("load_plan.id", ondelete="CASCADE"), nullable=True
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        SAEnum(
            NotificationChannel,
            name="notification_channel_enum",
            validate_strings=True,
            create_constraint=True,
        ),
        nullable=False,
    )
    destination: Mapped[str] = mapped_column(String(512), nullable=False)
    # Column uses trailing underscore to avoid SQL keyword collision; attribute
    # name is `trigger` for ergonomics. SQLAlchemy quotes it where needed.
    trigger: Mapped[NotificationTrigger] = mapped_column(
        "trigger_",
        SAEnum(
            NotificationTrigger,
            name="notification_trigger_enum",
            validate_strings=True,
            create_constraint=True,
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship("User")
    plan: Mapped[Optional["LoadPlan"]] = relationship("LoadPlan")
    deliveries: Mapped[list["NotificationDelivery"]] = relationship(
        "NotificationDelivery",
        back_populates="subscription",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # NOTE: SQL treats NULLs as distinct in UNIQUE constraints, so two
        # "all plans" subscriptions with the same (user, channel, destination)
        # are technically allowed. Acceptable for MVP; revisit alongside the
        # RBAC/visibility work when plan_id semantics are formalised.
        UniqueConstraint(
            "user_id", "plan_id", "channel", "destination",
            name="uq_notification_subscription_user_plan_channel_destination",
        ),
    )
