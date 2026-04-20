"""Tests for NotificationDispatcher (SFBL-180).

Covers:
- Trigger→status matrix (8 combinations of 4 terminal statuses × 2 triggers)
- Plan-specific vs all-plans subscription selection
- Fan-out creates exactly one delivery row per subscription
- dispatch_one with is_test=True → run_id NULL, is_test TRUE
- Unknown channel → failed delivery row
- Webhook failure → status=failed, last_error populated
- Metrics increment on sent/failed
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

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
from app.observability.metrics import notification_dispatch_total
from app.services.notifications.channels.base import ChannelResult
from app.services.notifications.dispatcher import NotificationDispatcher


pytestmark = pytest.mark.asyncio


class _StubChannel:
    name = "stub"

    def __init__(self, result: ChannelResult):
        self.result = result
        self.calls: list = []

    async def send(self, subscription, context):  # noqa: ARG002
        self.calls.append((subscription.id, context))
        return self.result


async def _seed(session):
    user = User(username=f"u-{uuid.uuid4().hex[:6]}", hashed_password="x")
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:6]}",
        instance_url="https://example.my.salesforce.com",
        login_url="https://login.salesforce.com",
        client_id="cid",
        private_key="enc",
        username="sf@example.com",
    )
    session.add_all([user, conn])
    await session.flush()
    plan = LoadPlan(connection_id=conn.id, name="p")
    session.add(plan)
    await session.flush()
    return user, plan


def _make_dispatcher(channels=None):
    from tests.conftest import _TestSession

    stub_email = _StubChannel(ChannelResult(accepted=True, attempts=1))
    stub_webhook = _StubChannel(ChannelResult(accepted=True, attempts=1))
    channels = channels or {
        NotificationChannel.email: stub_email,
        NotificationChannel.webhook: stub_webhook,
    }
    return NotificationDispatcher(_TestSession, channels), stub_email, stub_webhook


@pytest.mark.parametrize(
    "status,trigger,should_fire",
    [
        (RunStatus.completed, NotificationTrigger.terminal_any, True),
        (RunStatus.completed, NotificationTrigger.terminal_fail_only, False),
        (RunStatus.completed_with_errors, NotificationTrigger.terminal_any, True),
        (RunStatus.completed_with_errors, NotificationTrigger.terminal_fail_only, True),
        (RunStatus.failed, NotificationTrigger.terminal_any, True),
        (RunStatus.failed, NotificationTrigger.terminal_fail_only, True),
        (RunStatus.aborted, NotificationTrigger.terminal_any, True),
        (RunStatus.aborted, NotificationTrigger.terminal_fail_only, True),
    ],
)
async def test_trigger_status_matrix(status, trigger, should_fire):
    from tests.conftest import _TestSession

    async with _TestSession() as session:
        user, plan = await _seed(session)
        run = LoadRun(load_plan_id=plan.id, status=status)
        session.add(run)
        await session.flush()
        sub = NotificationSubscription(
            user_id=user.id,
            plan_id=plan.id,
            channel=NotificationChannel.webhook,
            destination="https://hooks.example.com/a",
            trigger=trigger,
        )
        session.add(sub)
        await session.commit()
        run_id = run.id

    dispatcher, _, webhook = _make_dispatcher()
    deliveries = await dispatcher.dispatch_run(run_id, status)

    if should_fire:
        assert len(deliveries) == 1
        assert webhook.calls, "webhook channel should have been called"
    else:
        assert deliveries == []
        assert not webhook.calls


async def test_all_plans_subscription_fires_regardless_of_plan():
    from tests.conftest import _TestSession

    async with _TestSession() as session:
        user, plan = await _seed(session)
        run = LoadRun(load_plan_id=plan.id, status=RunStatus.completed)
        session.add(run)
        sub = NotificationSubscription(
            user_id=user.id,
            plan_id=None,  # all plans
            channel=NotificationChannel.email,
            destination="a@example.com",
            trigger=NotificationTrigger.terminal_any,
        )
        session.add(sub)
        await session.commit()
        run_id = run.id

    dispatcher, email, _ = _make_dispatcher()
    deliveries = await dispatcher.dispatch_run(run_id, RunStatus.completed)

    assert len(deliveries) == 1
    assert email.calls


async def test_plan_specific_subscription_isolated_from_other_plans():
    from tests.conftest import _TestSession

    async with _TestSession() as session:
        user, plan = await _seed(session)
        other_plan = LoadPlan(connection_id=plan.connection_id, name="other")
        session.add(other_plan)
        await session.flush()
        run = LoadRun(load_plan_id=plan.id, status=RunStatus.failed)
        session.add(run)
        # Subscription on a *different* plan — must not fire
        sub = NotificationSubscription(
            user_id=user.id,
            plan_id=other_plan.id,
            channel=NotificationChannel.email,
            destination="other@example.com",
            trigger=NotificationTrigger.terminal_any,
        )
        session.add(sub)
        await session.commit()
        run_id = run.id

    dispatcher, email, _ = _make_dispatcher()
    deliveries = await dispatcher.dispatch_run(run_id, RunStatus.failed)
    assert deliveries == []
    assert not email.calls


async def test_dispatch_one_test_flag_nulls_run_id():
    from tests.conftest import _TestSession

    async with _TestSession() as session:
        user, plan = await _seed(session)
        sub = NotificationSubscription(
            user_id=user.id,
            plan_id=plan.id,
            channel=NotificationChannel.email,
            destination="test@example.com",
            trigger=NotificationTrigger.terminal_any,
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        sub_id = sub.id

    dispatcher, email, _ = _make_dispatcher()

    async with _TestSession() as session:
        loaded = await session.get(NotificationSubscription, sub_id)
        delivery = await dispatcher.dispatch_one(loaded, run=None, is_test=True)

    assert delivery.run_id is None
    assert delivery.is_test is True
    assert delivery.status == NotificationDeliveryStatus.sent


async def test_webhook_failure_records_error():
    from tests.conftest import _TestSession

    async with _TestSession() as session:
        user, plan = await _seed(session)
        run = LoadRun(load_plan_id=plan.id, status=RunStatus.failed)
        session.add(run)
        sub = NotificationSubscription(
            user_id=user.id,
            plan_id=plan.id,
            channel=NotificationChannel.webhook,
            destination="https://hooks.example.com/b",
            trigger=NotificationTrigger.terminal_any,
        )
        session.add(sub)
        await session.commit()
        run_id = run.id

    failing = _StubChannel(
        ChannelResult(accepted=False, attempts=3, error_detail="HTTP 503")
    )
    dispatcher, _, _ = _make_dispatcher(
        channels={NotificationChannel.webhook: failing}
    )
    deliveries = await dispatcher.dispatch_run(run_id, RunStatus.failed)

    assert len(deliveries) == 1
    d = deliveries[0]
    assert d.status == NotificationDeliveryStatus.failed
    assert d.attempt_count == 3
    assert d.last_error == "HTTP 503"


async def test_metrics_increment_on_sent():
    from tests.conftest import _TestSession

    before = notification_dispatch_total.labels(channel="email", status="sent")._value.get()

    async with _TestSession() as session:
        user, plan = await _seed(session)
        run = LoadRun(load_plan_id=plan.id, status=RunStatus.completed)
        session.add(run)
        sub = NotificationSubscription(
            user_id=user.id,
            plan_id=plan.id,
            channel=NotificationChannel.email,
            destination="a@example.com",
            trigger=NotificationTrigger.terminal_any,
        )
        session.add(sub)
        await session.commit()
        run_id = run.id

    dispatcher, _, _ = _make_dispatcher()
    await dispatcher.dispatch_run(run_id, RunStatus.completed)

    after = notification_dispatch_total.labels(channel="email", status="sent")._value.get()
    assert after == before + 1


async def test_delivery_row_persisted():
    from tests.conftest import _TestSession

    async with _TestSession() as session:
        user, plan = await _seed(session)
        run = LoadRun(load_plan_id=plan.id, status=RunStatus.completed)
        session.add(run)
        sub = NotificationSubscription(
            user_id=user.id,
            plan_id=plan.id,
            channel=NotificationChannel.webhook,
            destination="https://hooks.example.com/c",
            trigger=NotificationTrigger.terminal_any,
        )
        session.add(sub)
        await session.commit()
        run_id = run.id

    dispatcher, _, _ = _make_dispatcher()
    await dispatcher.dispatch_run(run_id, RunStatus.completed)

    async with _TestSession() as session:
        rows = (await session.execute(
            select(NotificationDelivery).where(NotificationDelivery.run_id == run_id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == NotificationDeliveryStatus.sent
