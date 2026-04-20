from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.notification_subscription import (
    NotificationChannel,
    NotificationTrigger,
)


class NotificationSubscriptionBase(BaseModel):
    plan_id: Optional[str] = None
    channel: NotificationChannel
    destination: str
    trigger: NotificationTrigger


class NotificationSubscriptionCreate(NotificationSubscriptionBase):
    pass


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
