"""Tests for NotificationSubscription and NotificationDelivery models (SFBL-179)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import selectinload

from app.models.connection import Connection
from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun, RunStatus
from app.models.notification_delivery import (
    NotificationDelivery,
    NotificationDeliveryStatus,
)
from app.models.notification_subscription import (
    NotificationChannel,
    NotificationSubscription,
    NotificationTrigger,
)
from app.models.user import User


pytestmark = pytest.mark.asyncio


async def _seed_user_and_plan(session) -> tuple[User, LoadPlan]:
    user = User(email=f"u-{uuid.uuid4().hex[:6]}@example.com", hashed_password="x")
    connection = Connection(
        name=f"c-{uuid.uuid4().hex[:6]}",
        instance_url="https://example.my.salesforce.com",
        login_url="https://login.salesforce.com",
        client_id="cid",
        private_key="encrypted",
        username="sf@example.com",
    )
    session.add_all([user, connection])
    await session.flush()
    plan = LoadPlan(connection_id=connection.id, name="p")
    session.add(plan)
    await session.flush()
    return user, plan


@pytest.fixture
async def db_session():
    """Yield a fresh AsyncSession bound to the test engine."""
    from tests.conftest import _TestSession

    session = _TestSession()
    try:
        yield session
    finally:
        await session.close()


async def test_create_subscription_with_plan(db_session):
    user, plan = await _seed_user_and_plan(db_session)
    sub = NotificationSubscription(
        user_id=user.id,
        plan_id=plan.id,
        channel=NotificationChannel.email,
        destination="alice@example.com",
        trigger=NotificationTrigger.terminal_any,
    )
    db_session.add(sub)
    await db_session.commit()
    await db_session.refresh(sub)
    assert sub.id
    assert sub.created_at is not None


async def test_create_subscription_null_plan_is_all_plans(db_session):
    user, _ = await _seed_user_and_plan(db_session)
    sub = NotificationSubscription(
        user_id=user.id,
        plan_id=None,
        channel=NotificationChannel.webhook,
        destination="https://hooks.slack.com/services/XYZ",
        trigger=NotificationTrigger.terminal_fail_only,
    )
    db_session.add(sub)
    await db_session.commit()
    assert sub.plan_id is None


async def test_unique_constraint_rejects_duplicate(db_session):
    user, plan = await _seed_user_and_plan(db_session)
    sub1 = NotificationSubscription(
        user_id=user.id,
        plan_id=plan.id,
        channel=NotificationChannel.email,
        destination="dup@example.com",
        trigger=NotificationTrigger.terminal_any,
    )
    db_session.add(sub1)
    await db_session.commit()

    sub2 = NotificationSubscription(
        user_id=user.id,
        plan_id=plan.id,
        channel=NotificationChannel.email,
        destination="dup@example.com",
        trigger=NotificationTrigger.terminal_fail_only,  # different trigger still collides
    )
    db_session.add(sub2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_invalid_channel_rejected(db_session):
    user, plan = await _seed_user_and_plan(db_session)
    sub = NotificationSubscription(
        user_id=user.id,
        plan_id=plan.id,
        channel="sms",  # not a valid enum member
        destination="x",
        trigger=NotificationTrigger.terminal_any,
    )
    db_session.add(sub)
    with pytest.raises((StatementError, LookupError)):
        await db_session.commit()
    await db_session.rollback()


async def test_invalid_trigger_rejected(db_session):
    user, plan = await _seed_user_and_plan(db_session)
    sub = NotificationSubscription(
        user_id=user.id,
        plan_id=plan.id,
        channel=NotificationChannel.email,
        destination="x@example.com",
        trigger="terminal_hard_fail_only",  # deliberately not in enum
    )
    db_session.add(sub)
    with pytest.raises((StatementError, LookupError)):
        await db_session.commit()
    await db_session.rollback()


async def test_delivery_allows_null_run_when_is_test_true(db_session):
    user, plan = await _seed_user_and_plan(db_session)
    sub = NotificationSubscription(
        user_id=user.id,
        plan_id=plan.id,
        channel=NotificationChannel.email,
        destination="test@example.com",
        trigger=NotificationTrigger.terminal_any,
    )
    db_session.add(sub)
    await db_session.flush()

    delivery = NotificationDelivery(
        subscription_id=sub.id,
        run_id=None,
        is_test=True,
        status=NotificationDeliveryStatus.sent,
        attempt_count=1,
        sent_at=datetime.now(timezone.utc),
    )
    db_session.add(delivery)
    await db_session.commit()
    await db_session.refresh(delivery)
    assert delivery.run_id is None
    assert delivery.is_test is True


async def test_delivery_links_to_real_run(db_session):
    user, plan = await _seed_user_and_plan(db_session)
    run = LoadRun(load_plan_id=plan.id, status=RunStatus.completed)
    db_session.add(run)
    sub = NotificationSubscription(
        user_id=user.id,
        plan_id=plan.id,
        channel=NotificationChannel.webhook,
        destination="https://example.com/hook",
        trigger=NotificationTrigger.terminal_any,
    )
    db_session.add(sub)
    await db_session.flush()
    delivery = NotificationDelivery(
        subscription_id=sub.id,
        run_id=run.id,
        status=NotificationDeliveryStatus.failed,
        attempt_count=3,
        last_error="connection refused",
    )
    db_session.add(delivery)
    await db_session.commit()
    assert delivery.run_id == run.id


async def test_relationships_load_without_n_plus_1(db_session):
    user, plan = await _seed_user_and_plan(db_session)
    sub = NotificationSubscription(
        user_id=user.id,
        plan_id=plan.id,
        channel=NotificationChannel.email,
        destination="a@example.com",
        trigger=NotificationTrigger.terminal_any,
    )
    db_session.add(sub)
    await db_session.flush()
    for _ in range(3):
        db_session.add(
            NotificationDelivery(
                subscription_id=sub.id,
                status=NotificationDeliveryStatus.sent,
            )
        )
    await db_session.commit()

    stmt = (
        select(NotificationSubscription)
        .where(NotificationSubscription.id == sub.id)
        .options(
            selectinload(NotificationSubscription.deliveries),
            selectinload(NotificationSubscription.user),
            selectinload(NotificationSubscription.plan),
        )
    )
    result = await db_session.execute(stmt)
    loaded = result.scalar_one()
    assert len(loaded.deliveries) == 3
    assert loaded.user.id == user.id
    assert loaded.plan is not None and loaded.plan.id == plan.id


async def test_cascade_delete_subscription_removes_deliveries(db_session):
    user, plan = await _seed_user_and_plan(db_session)
    sub = NotificationSubscription(
        user_id=user.id,
        plan_id=plan.id,
        channel=NotificationChannel.webhook,
        destination="https://example.com/h",
        trigger=NotificationTrigger.terminal_any,
    )
    db_session.add(sub)
    await db_session.flush()
    db_session.add(
        NotificationDelivery(
            subscription_id=sub.id, status=NotificationDeliveryStatus.sent
        )
    )
    await db_session.commit()
    await db_session.delete(sub)
    await db_session.commit()

    remaining = await db_session.execute(
        select(NotificationDelivery).where(
            NotificationDelivery.subscription_id == sub.id
        )
    )
    assert remaining.first() is None
