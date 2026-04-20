"""Notification subscriptions API (SFBL-182).

CRUD for per-user subscriptions plus a ``/test`` endpoint that fires a
synthetic payload through the real dispatcher code path so the UI can
verify a destination without waiting for a real run to finish.

Profile guard: all routes return 403 when ``APP_DISTRIBUTION=desktop``
(``auth_mode=none``) since there is no meaningful user identity in that
profile.  To be revisited when RBAC lands.
"""

from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.load_plan import LoadPlan
from app.models.notification_subscription import (
    NotificationChannel,
    NotificationSubscription,
)
from app.models.user import User
from app.schemas.notification_subscription import (
    NotificationSubscriptionCreate,
    NotificationSubscriptionResponse,
    NotificationSubscriptionUpdate,
    NotificationTestResponse,
)
from app.services.auth import get_current_user
from app.services.notifications import get_notification_dispatcher

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/notification-subscriptions",
    tags=["notifications"],
    dependencies=[Depends(get_current_user)],
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _block_desktop_profile() -> None:
    """Per-route guard: notifications are disabled on the desktop profile."""
    if settings.auth_mode == "none":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Notifications are not available in this distribution",
        )


async def _get_owned_or_error(
    subscription_id: str,
    db: AsyncSession,
    user: User,
) -> NotificationSubscription:
    sub = await db.get(NotificationSubscription, subscription_id)
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )
    if sub.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorised for this subscription",
        )
    return sub


async def _validate_plan_id(plan_id: str | None, db: AsyncSession) -> None:
    if plan_id is None:
        return
    plan = await db.get(LoadPlan, plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown plan_id: {plan_id}",
        )


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get("", response_model=List[NotificationSubscriptionResponse])
async def list_subscriptions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[NotificationSubscription]:
    _block_desktop_profile()
    result = await db.execute(
        select(NotificationSubscription)
        .where(NotificationSubscription.user_id == user.id)
        .order_by(NotificationSubscription.created_at.desc())
    )
    return list(result.scalars().all())


@router.post(
    "",
    response_model=NotificationSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_subscription(
    data: NotificationSubscriptionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NotificationSubscription:
    _block_desktop_profile()
    await _validate_plan_id(data.plan_id, db)

    sub = NotificationSubscription(
        user_id=user.id,
        plan_id=data.plan_id,
        channel=data.channel,
        destination=data.destination,
        trigger=data.trigger,
    )
    db.add(sub)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A subscription with the same destination already exists for this plan",
        )
    await db.refresh(sub)
    return sub


@router.get("/{subscription_id}", response_model=NotificationSubscriptionResponse)
async def get_subscription(
    subscription_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NotificationSubscription:
    _block_desktop_profile()
    return await _get_owned_or_error(subscription_id, db, user)


@router.put("/{subscription_id}", response_model=NotificationSubscriptionResponse)
async def update_subscription(
    subscription_id: str,
    data: NotificationSubscriptionUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NotificationSubscription:
    _block_desktop_profile()
    sub = await _get_owned_or_error(subscription_id, db, user)
    update_data = data.model_dump(exclude_unset=True)

    if "plan_id" in update_data:
        await _validate_plan_id(update_data["plan_id"], db)

    # If channel or destination changes, re-run the destination validator
    # using the *resulting* channel so http/email rules still apply.
    if "destination" in update_data or "channel" in update_data:
        from app.schemas.notification_subscription import _validate_destination

        new_channel = update_data.get("channel", sub.channel)
        if isinstance(new_channel, str):
            new_channel = NotificationChannel(new_channel)
        new_destination = update_data.get("destination", sub.destination)
        try:
            update_data["destination"] = _validate_destination(
                new_destination, new_channel
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )

    for field, value in update_data.items():
        setattr(sub, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A subscription with the same destination already exists for this plan",
        )
    await db.refresh(sub)
    return sub


@router.delete("/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subscription(
    subscription_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    _block_desktop_profile()
    sub = await _get_owned_or_error(subscription_id, db, user)
    await db.delete(sub)
    await db.commit()


@router.post(
    "/{subscription_id}/test",
    response_model=NotificationTestResponse,
)
async def test_subscription(
    subscription_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NotificationTestResponse:
    _block_desktop_profile()
    sub = await _get_owned_or_error(subscription_id, db, user)

    dispatcher = get_notification_dispatcher()
    if dispatcher is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Notification dispatcher not initialised",
        )

    delivery = await dispatcher.dispatch_one(sub, run=None, is_test=True)
    return NotificationTestResponse(
        delivery_id=delivery.id,
        status=delivery.status.value if hasattr(delivery.status, "value") else str(delivery.status),
        attempts=delivery.attempt_count,
        last_error=delivery.last_error,
        email_delivery_id=delivery.email_delivery_id,
    )
