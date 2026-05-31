"""Pydantic schemas for notification subscriptions (SFBL-179, SFBL-182)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.notification_subscription import (
    NotificationChannel,
    NotificationTrigger,
)


LABEL_MAX_LENGTH = 120


def _normalize_label(label: Optional[str]) -> Optional[str]:
    """Trim whitespace, coerce blank to None, enforce max length."""
    if label is None:
        return None
    label = label.strip()
    if not label:
        return None
    if len(label) > LABEL_MAX_LENGTH:
        raise ValueError(f"label must be at most {LABEL_MAX_LENGTH} characters")
    return label


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
    elif channel in (
        NotificationChannel.webhook,
        NotificationChannel.teams_webhook,
    ):
        if not destination.lower().startswith("https://"):
            raise ValueError("webhook destination must use https://")
    return destination


class NotificationSubscriptionBase(BaseModel):
    plan_id: Optional[str] = None
    label: Optional[str] = None
    channel: NotificationChannel
    destination: str
    trigger: NotificationTrigger

    @field_validator("label")
    @classmethod
    def _normalize_label(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_label(v)


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
    label: Optional[str] = None
    channel: Optional[NotificationChannel] = None
    destination: Optional[str] = None
    trigger: Optional[NotificationTrigger] = None

    @field_validator("label")
    @classmethod
    def _normalize_label(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_label(v)


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
