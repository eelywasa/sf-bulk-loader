"""Unit tests for TeamsWebhookChannel (SFBL-352)."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.models.notification_subscription import NotificationChannel
from app.services.notifications.channels.teams import TeamsWebhookChannel


pytestmark = pytest.mark.asyncio


def _sub(
    url: str = "https://example.powerplatform.com/triggers/manual/paths/invoke",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        destination=url,
        channel=NotificationChannel.teams_webhook,
    )


def _client_factory(handler):
    transport = httpx.MockTransport(handler)

    def _factory():
        return httpx.AsyncClient(transport=transport, timeout=5.0)

    return _factory


def _context() -> dict:
    return {
        "run": {
            "id": "r1",
            "status": "completed",
            "plan_name": "My Plan",
            "total_records": 1000,
            "total_success": 990,
            "total_errors": 10,
        }
    }


async def test_teams_202_accepted_one_attempt():
    """Teams Workflows trigger returns 202 on acceptance — that is success."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(202)

    channel = TeamsWebhookChannel(client_factory=_client_factory(handler))
    result = await channel.send(_sub(), _context())

    assert result.accepted is True
    assert result.attempts == 1
    assert len(calls) == 1


async def test_teams_payload_is_adaptive_card_envelope():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        return httpx.Response(202)

    channel = TeamsWebhookChannel(client_factory=_client_factory(handler))
    await channel.send(_sub(), _context())

    body = captured["body"]
    assert body["type"] == "message"
    attachment = body["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"

    card = attachment["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.4"

    # Banner container is "good" for a clean completion.
    container = card["body"][0]
    assert container["type"] == "Container"
    assert container["style"] == "good"

    # FactSet carries run metadata.
    factset = card["body"][1]
    assert factset["type"] == "FactSet"
    titles = {f["title"] for f in factset["facts"]}
    assert {"Plan", "Status", "Rows", "Failed"} <= titles

    # Deep link back into the run view.
    action = card["actions"][0]
    assert action["type"] == "Action.OpenUrl"
    assert action["url"].endswith("/runs/r1")


async def test_teams_failed_status_renders_attention_banner():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        return httpx.Response(202)

    channel = TeamsWebhookChannel(client_factory=_client_factory(handler))
    ctx = {"run": {"id": "r2", "status": "failed", "plan_name": "Doomed"}}
    await channel.send(_sub(), ctx)

    container = captured["body"]["attachments"][0]["content"]["body"][0]
    assert container["style"] == "attention"


async def test_teams_retries_on_5xx_then_succeeds(monkeypatch):
    import app.services.notifications.channels._shared as mod

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)
    responses = iter([500, 502, 202])

    def handler(_req):
        return httpx.Response(next(responses))

    channel = TeamsWebhookChannel(client_factory=_client_factory(handler))
    result = await channel.send(_sub(), _context())

    assert result.accepted is True
    assert result.attempts == 3


async def test_teams_4xx_is_terminal_no_retry():
    calls = 0

    def handler(_req):
        nonlocal calls
        calls += 1
        return httpx.Response(400)

    channel = TeamsWebhookChannel(client_factory=_client_factory(handler))
    result = await channel.send(_sub(), _context())

    assert result.accepted is False
    assert result.attempts == 1
    assert calls == 1
    assert "400" in (result.error_detail or "")


async def test_teams_429_retries_as_throttled(monkeypatch):
    import app.services.notifications.channels._shared as mod

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)
    responses = iter([429, 202])

    def handler(_req):
        return httpx.Response(next(responses))

    channel = TeamsWebhookChannel(client_factory=_client_factory(handler))
    result = await channel.send(_sub(), _context())

    assert result.accepted is True
    assert result.attempts == 2


async def test_teams_network_error_retries_and_exhausts(monkeypatch):
    import app.services.notifications.channels._shared as mod

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

    def handler(_req):
        raise httpx.ConnectError("nope")

    channel = TeamsWebhookChannel(client_factory=_client_factory(handler))
    result = await channel.send(_sub(), _context())

    assert result.accepted is False
    assert result.attempts == 3
    assert result.error_detail is not None
