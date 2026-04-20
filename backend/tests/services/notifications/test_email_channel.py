"""Unit tests for EmailChannel (SFBL-180)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.notification_subscription import NotificationChannel
from app.services.email.message import EmailCategory
from app.services.notifications.channels.email import EmailChannel


pytestmark = pytest.mark.asyncio


def _sub(addr: str = "alice@example.com") -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        destination=addr,
        channel=NotificationChannel.email,
    )


class _FakeDelivery:
    def __init__(self, status: str, err: str | None = None, did: str | None = None):
        self.id = did or str(uuid.uuid4())
        self.status = status
        self.last_error_msg = err


class _FakeEmailService:
    def __init__(self, result: _FakeDelivery | Exception):
        self._result = result
        self.calls: list[tuple[str, dict, dict]] = []

    async def send_template(self, template_name, context, *, to, category, **kwargs):
        self.calls.append(
            ({"template": template_name, "to": to, "category": category}, dict(context), kwargs)
        )
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def test_email_channel_success_returns_pointer():
    delivery = _FakeDelivery(status="sent", did="deliv-123")
    svc = _FakeEmailService(delivery)
    channel = EmailChannel(svc)  # type: ignore[arg-type]

    result = await channel.send(_sub(), {"run": {"id": "r1"}})

    assert result.accepted is True
    assert result.email_delivery_id == "deliv-123"
    assert result.attempts == 1
    assert svc.calls[0][0]["template"] == "notifications/run_complete"
    assert svc.calls[0][0]["category"] == EmailCategory.NOTIFICATION
    assert svc.calls[0][0]["to"] == "alice@example.com"


async def test_email_channel_skipped_treated_as_accepted():
    delivery = _FakeDelivery(status="skipped", did="deliv-skip")
    channel = EmailChannel(_FakeEmailService(delivery))  # type: ignore[arg-type]
    result = await channel.send(_sub(), {})
    assert result.accepted is True


async def test_email_channel_failed_surfaces_error():
    delivery = _FakeDelivery(status="failed", err="SMTP refused", did="deliv-fail")
    channel = EmailChannel(_FakeEmailService(delivery))  # type: ignore[arg-type]
    result = await channel.send(_sub(), {})
    assert result.accepted is False
    assert result.error_detail == "SMTP refused"
    assert result.email_delivery_id == "deliv-fail"


async def test_email_channel_flattens_dispatcher_context_for_template():
    """Dispatcher hands over {"run": {...}, "is_test": ..., "text": ...}.

    The ``notifications/run_complete`` template enforces a strict flat key
    contract; EmailChannel must project dispatcher context down to the exact
    keys the manifest requires.
    """
    delivery = _FakeDelivery(status="sent")
    svc = _FakeEmailService(delivery)
    channel = EmailChannel(svc)  # type: ignore[arg-type]

    dispatcher_context = {
        "run": {
            "id": "run-xyz",
            "plan_name": "Q4 Load",
            "status": "completed",
            "total_records": 1000,
            "total_success": 995,
            "total_errors": 5,
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:05:00+00:00",
        },
        "is_test": False,
        "text": "anything",
    }
    await channel.send(_sub(), dispatcher_context)

    template_ctx = svc.calls[0][1]
    assert set(template_ctx.keys()) == {
        "plan_name",
        "run_id",
        "status",
        "total_rows",
        "success_rows",
        "failed_rows",
        "started_at",
        "ended_at",
        "run_url",
    }
    assert template_ctx["plan_name"] == "Q4 Load"
    assert template_ctx["run_id"] == "run-xyz"
    assert template_ctx["total_rows"] == 1000
    assert template_ctx["success_rows"] == 995
    assert template_ctx["failed_rows"] == 5
    assert template_ctx["run_url"].endswith("/runs/run-xyz")


async def test_email_channel_flatten_defaults_when_keys_missing():
    delivery = _FakeDelivery(status="sent")
    svc = _FakeEmailService(delivery)
    channel = EmailChannel(svc)  # type: ignore[arg-type]
    await channel.send(_sub(), {"run": {"id": "r1", "status": "failed"}, "is_test": True})
    ctx = svc.calls[0][1]
    assert ctx["plan_name"] == "Untitled plan"
    assert ctx["total_rows"] == 0
    assert ctx["success_rows"] == 0
    assert ctx["failed_rows"] == 0
    assert ctx["started_at"] == ""
    assert ctx["ended_at"] == ""


async def test_email_channel_exception_is_caught_and_sanitised():
    channel = EmailChannel(_FakeEmailService(RuntimeError("boom")))  # type: ignore[arg-type]
    result = await channel.send(_sub(), {})
    assert result.accepted is False
    assert result.error_detail == "boom"
