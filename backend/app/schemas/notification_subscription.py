"""Pydantic schemas for notification subscriptions (SFBL-179, SFBL-182)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.notification_subscription import (
    NotificationChannel,
    NotificationTrigger,
)


def _validate_destination(
    destination: str, channel: NotificationChannel
) -> str:
    destination = destination.strip()
    if not destination:
        raise ValueError("destination must not be empty")
    if channel == NotificationChannel.email:
        # Lightweight check — we don't want to hit DNS in tests.
        # Full validation still happens server-side before the dispatch.
        if "@" not in destination or destination.startswith("@") or destination.endswith("@"):
            raise ValueError("destination is not a valid email address")
    elif channel == NotificationChannel.webhook:
        if not destination.lower().startswith("https://"):
            raise ValueError("webhook destination must use https://")
    return destination


class NotificationSubscriptionBase(BaseModel):
    plan_id: Optional[str] = None
    channel: NotificationChannel
    destination: str
    trigger: NotificationTrigger


class NotificationSubscriptionCreate(NotificationSubscriptionBase):
    @field_validator("destination")
    @classmethod
    def _validate_destination(cls, v: str, info) -> str:
        channel = info.data.get("channel")
        if channel is None:
            return v
        return _validate_destination(v, channel)


class NotificationSubscriptionUpdate(BaseModel):
    plan_id: Optional[str] = None
    channel: Optional[NotificationChannel] = None
    destination: Optional[str] = None
    trigger: Optional[NotificationTrigger] = None


class NotificationSubscriptionResponse(NotificationSubscriptionBase):
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationTestResponse(BaseModel):
    """Result returned from ``POST /notification-subscriptions/{id}/test``."""

    delivery_id: str
    status: str
    attempts: int
    last_error: Optional[str] = None
    email_delivery_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
