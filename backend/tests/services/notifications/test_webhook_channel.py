"""Unit tests for WebhookChannel (SFBL-180)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.models.notification_subscription import NotificationChannel
from app.services.notifications.channels.webhook import WebhookChannel


pytestmark = pytest.mark.asyncio


def _sub(url: str = "https://hooks.example.com/services/XXX/YYY") -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        destination=url,
        channel=NotificationChannel.webhook,
    )


def _client_factory(handler):
    transport = httpx.MockTransport(handler)

    def _factory():
        return httpx.AsyncClient(transport=transport, timeout=5.0)

    return _factory


def _context() -> dict:
    return {"run": {"id": "r1", "status": "completed", "plan_name": "My Plan"}}


async def test_webhook_2xx_accepted_one_attempt(monkeypatch):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    channel = WebhookChannel(client_factory=_client_factory(handler))
    result = await channel.send(_sub(), _context())

    assert result.accepted is True
    assert result.attempts == 1
    assert len(calls) == 1
    body = calls[0].read()
    assert b"My Plan" in body


async def test_webhook_retries_on_5xx_then_succeeds(monkeypatch):
    # Remove the jitter sleep to keep the test fast
    import app.services.notifications.channels.webhook as mod

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)
    responses = iter([500, 502, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(responses))

    channel = WebhookChannel(client_factory=_client_factory(handler))
    result = await channel.send(_sub(), _context())

    assert result.accepted is True
    assert result.attempts == 3


async def test_webhook_exhausted_5xx_is_failed(monkeypatch):
    import app.services.notifications.channels.webhook as mod

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

    def handler(_req):
        return httpx.Response(503)

    channel = WebhookChannel(client_factory=_client_factory(handler))
    result = await channel.send(_sub(), _context())

    assert result.accepted is False
    assert result.attempts == 3
    assert "503" in (result.error_detail or "")


async def test_webhook_4xx_is_terminal_no_retry(monkeypatch):
    calls = 0

    def handler(_req):
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    channel = WebhookChannel(client_factory=_client_factory(handler))
    result = await channel.send(_sub(), _context())

    assert result.accepted is False
    assert result.attempts == 1
    assert calls == 1
    assert "404" in (result.error_detail or "")


async def test_webhook_429_retries_as_throttled(monkeypatch):
    import app.services.notifications.channels.webhook as mod

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)
    responses = iter([429, 200])

    def handler(_req):
        return httpx.Response(next(responses))

    channel = WebhookChannel(client_factory=_client_factory(handler))
    result = await channel.send(_sub(), _context())

    assert result.accepted is True
    assert result.attempts == 2


async def test_webhook_retry_metric_only_increments_on_actual_retry(monkeypatch):
    """Metric must reflect retries scheduled, not failed attempts.

    Budget = 3 attempts, so a run of three 500s should emit at most two
    retry events (one after attempt 1, one after attempt 2).  The final
    attempt yields the terminal failure and must NOT increment the metric.
    """
    import app.services.notifications.channels.webhook as mod
    from app.observability.metrics import notification_webhook_retry_total

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

    before = notification_webhook_retry_total.labels(reason="server_error")._value.get()

    def handler(_req):
        return httpx.Response(500)

    channel = WebhookChannel(client_factory=_client_factory(handler))
    result = await channel.send(_sub(), _context())

    assert result.accepted is False
    assert result.attempts == 3
    after = notification_webhook_retry_total.labels(reason="server_error")._value.get()
    assert after - before == 2  # retries 1→2 and 2→3; no increment on exhaust


async def test_webhook_network_error_retries_and_exhausts(monkeypatch):
    import app.services.notifications.channels.webhook as mod

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

    def handler(_req):
        raise httpx.ConnectError("nope")

    channel = WebhookChannel(client_factory=_client_factory(handler))
    result = await channel.send(_sub(), _context())

    assert result.accepted is False
    assert result.attempts == 3
    assert result.error_detail is not None
