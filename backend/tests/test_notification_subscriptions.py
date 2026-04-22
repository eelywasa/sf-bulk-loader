"""Tests for /api/notification-subscriptions (SFBL-182)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.connection import Connection
from app.models.load_plan import LoadPlan
from app.models.notification_subscription import (
    NotificationChannel,
    NotificationSubscription,
    NotificationTrigger,
)
from app.models.user import User
from app.services.auth import get_current_user
from app.database import get_db




def _seed_user(session_maker, user: User) -> None:
    async def _do() -> None:
        async with session_maker() as s:
            s.add(user)
            await s.commit()

    asyncio.get_event_loop().run_until_complete(_do())


def _seed_plan(session_maker, plan_id: str) -> None:
    conn_id = str(uuid.uuid4())

    async def _do() -> None:
        async with session_maker() as s:
            s.add(
                Connection(
                    id=conn_id,
                    name=f"conn-{conn_id[:6]}",
                    instance_url="https://example.my.salesforce.com",
                    login_url="https://login.salesforce.com",
                    client_id="fake",
                    private_key="fake",
                    username="fake@example.com",
                    is_sandbox=False,
                )
            )
            await s.commit()
            s.add(
                LoadPlan(
                    id=plan_id,
                    name=f"plan-{plan_id[:6]}",
                    connection_id=conn_id,
                    max_parallel_jobs=1,
                    error_threshold_pct=100,
                    abort_on_step_failure=False,
                )
            )
            await s.commit()

    asyncio.get_event_loop().run_until_complete(_do())


@pytest.fixture
def sub_client(tmp_path, monkeypatch) -> Iterator[tuple[TestClient, User]]:
    """TestClient with an authenticated user that is persisted in the DB."""
    from tests.conftest import _TestSession  # type: ignore

    user = User(
        id=str(uuid.uuid4()),
        email="sub-user@example.com",
        hashed_password="x",
        status="active",
    )
    _seed_user(_TestSession, user)

    async def override_get_db():
        async with _TestSession() as session:
            yield session

    async def override_get_current_user():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c, user
    finally:
        app.dependency_overrides.clear()


# ── Create ───────────────────────────────────────────────────────────────────


def test_create_email_subscription_returns_201(sub_client):
    client, user = sub_client
    payload = {
        "channel": "email",
        "destination": "alice@example.com",
        "trigger": "terminal_any",
    }
    resp = client.post("/api/notification-subscriptions", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["user_id"] == user.id
    assert body["channel"] == "email"
    assert body["destination"] == "alice@example.com"
    assert body["trigger"] == "terminal_any"
    assert body["plan_id"] is None


def test_create_webhook_requires_https(sub_client):
    client, _ = sub_client
    resp = client.post(
        "/api/notification-subscriptions",
        json={
            "channel": "webhook",
            "destination": "http://hooks.slack.com/services/T/B/X",
            "trigger": "terminal_any",
        },
    )
    assert resp.status_code == 422


def test_create_rejects_invalid_email(sub_client):
    client, _ = sub_client
    resp = client.post(
        "/api/notification-subscriptions",
        json={
            "channel": "email",
            "destination": "not-an-email",
            "trigger": "terminal_any",
        },
    )
    assert resp.status_code == 422


def test_create_unknown_plan_id_returns_422(sub_client):
    client, _ = sub_client
    resp = client.post(
        "/api/notification-subscriptions",
        json={
            "plan_id": str(uuid.uuid4()),
            "channel": "email",
            "destination": "a@b.com",
            "trigger": "terminal_any",
        },
    )
    assert resp.status_code == 422


def test_create_duplicate_returns_409(sub_client):
    from tests.conftest import _TestSession  # type: ignore

    client, _ = sub_client
    # Unique constraint treats NULLs as distinct, so we need a concrete plan_id.
    plan_id = str(uuid.uuid4())
    _seed_plan(_TestSession, plan_id)

    payload = {
        "plan_id": plan_id,
        "channel": "email",
        "destination": "dup@example.com",
        "trigger": "terminal_any",
    }
    assert client.post("/api/notification-subscriptions", json=payload).status_code == 201
    resp = client.post("/api/notification-subscriptions", json=payload)
    assert resp.status_code == 409


# ── List + get ───────────────────────────────────────────────────────────────


def test_list_filters_by_current_user(sub_client):
    from tests.conftest import _TestSession  # type: ignore

    client, user = sub_client
    # Create a subscription for current user
    client.post(
        "/api/notification-subscriptions",
        json={"channel": "email", "destination": "mine@x.com", "trigger": "terminal_any"},
    )
    # Insert one for a different user directly
    other_user_id = str(uuid.uuid4())

    async def _seed_other():
        async with _TestSession() as s:
            s.add(User(id=other_user_id, email="other@example.com", hashed_password="x",
                       status="active"))
            await s.commit()
            s.add(NotificationSubscription(
                user_id=other_user_id,
                plan_id=None,
                channel=NotificationChannel.email,
                destination="other@x.com",
                trigger=NotificationTrigger.terminal_any,
            ))
            await s.commit()

    asyncio.get_event_loop().run_until_complete(_seed_other())

    resp = client.get("/api/notification-subscriptions")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["user_id"] == user.id


def test_get_cross_user_returns_403(sub_client):
    from tests.conftest import _TestSession  # type: ignore

    client, _ = sub_client
    other_user_id = str(uuid.uuid4())
    other_sub_id = str(uuid.uuid4())

    async def _seed():
        async with _TestSession() as s:
            s.add(User(id=other_user_id, email="victim@example.com", hashed_password="x",
                       status="active"))
            await s.commit()
            s.add(NotificationSubscription(
                id=other_sub_id,
                user_id=other_user_id,
                plan_id=None,
                channel=NotificationChannel.email,
                destination="victim@x.com",
                trigger=NotificationTrigger.terminal_any,
            ))
            await s.commit()

    asyncio.get_event_loop().run_until_complete(_seed())

    resp = client.get(f"/api/notification-subscriptions/{other_sub_id}")
    assert resp.status_code == 403


# ── Update + delete ──────────────────────────────────────────────────────────


def test_update_destination(sub_client):
    client, _ = sub_client
    created = client.post(
        "/api/notification-subscriptions",
        json={"channel": "email", "destination": "old@x.com", "trigger": "terminal_any"},
    ).json()
    resp = client.put(
        f"/api/notification-subscriptions/{created['id']}",
        json={"destination": "new@x.com"},
    )
    assert resp.status_code == 200
    assert resp.json()["destination"] == "new@x.com"


def test_update_rejects_invalid_destination(sub_client):
    client, _ = sub_client
    created = client.post(
        "/api/notification-subscriptions",
        json={"channel": "webhook", "destination": "https://hooks.x.com/y", "trigger": "terminal_any"},
    ).json()
    resp = client.put(
        f"/api/notification-subscriptions/{created['id']}",
        json={"destination": "http://nope.com/y"},
    )
    assert resp.status_code == 422


def test_delete_subscription(sub_client):
    client, _ = sub_client
    created = client.post(
        "/api/notification-subscriptions",
        json={"channel": "email", "destination": "bye@x.com", "trigger": "terminal_any"},
    ).json()
    resp = client.delete(f"/api/notification-subscriptions/{created['id']}")
    assert resp.status_code == 204
    assert client.get(f"/api/notification-subscriptions/{created['id']}").status_code == 404


# ── /test ────────────────────────────────────────────────────────────────────


def test_test_endpoint_uses_real_dispatcher(sub_client, monkeypatch):
    from app.services import notifications as notif_module
    from app.services.notifications.dispatcher import NotificationDispatcher

    client, _ = sub_client
    created = client.post(
        "/api/notification-subscriptions",
        json={"channel": "email", "destination": "test@x.com", "trigger": "terminal_any"},
    ).json()

    # Stub out the channel so we don't actually send email
    class _Channel:
        name = "email"

        async def send(self, subscription, context):
            from app.services.notifications.channels.base import ChannelResult
            return ChannelResult(accepted=True, attempts=1, email_delivery_id=None)

    from tests.conftest import _TestSession  # type: ignore
    dispatcher = NotificationDispatcher(
        session_factory=_TestSession,
        channels={NotificationChannel.email: _Channel()},
    )
    monkeypatch.setattr(notif_module, "_dispatcher", dispatcher)

    resp = client.post(f"/api/notification-subscriptions/{created['id']}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "sent"
    assert body["attempts"] == 1

    # Confirm a notification_delivery row was written with is_test=TRUE and run_id=NULL
    from app.models.notification_delivery import NotificationDelivery

    async def _check():
        async with _TestSession() as s:
            from sqlalchemy import select

            rows = (await s.execute(select(NotificationDelivery))).scalars().all()
            assert len(rows) == 1
            assert rows[0].is_test is True
            assert rows[0].run_id is None

    asyncio.get_event_loop().run_until_complete(_check())


# ── Desktop profile guard ────────────────────────────────────────────────────


def test_desktop_profile_returns_403(sub_client, monkeypatch):
    from app.api import notification_subscriptions as mod

    client, _ = sub_client
    monkeypatch.setattr(mod.settings, "auth_mode", "none")
    resp = client.get("/api/notification-subscriptions")
    assert resp.status_code == 403
