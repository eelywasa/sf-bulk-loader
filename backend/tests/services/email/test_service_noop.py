"""Tests for EmailService with NoopBackend.

Covers:
- Happy path returns status=skipped
- Duplicate idempotency_key returns existing row without calling backend
"""

from __future__ import annotations

import pytest

from app.models.email_delivery import DeliveryStatus
from app.services.email.backends.noop import NoopBackend
from app.services.email.message import EmailCategory, EmailMessage
from app.services.email.service import EmailService
from tests.services.email.conftest import EmailTestSession


def _service() -> EmailService:
    return EmailService(backend=NoopBackend(), session_factory=EmailTestSession)


def _msg(to: str = "user@example.com") -> EmailMessage:
    return EmailMessage(to=to, subject="Test Subject", text_body="Test body text.")


class TestNoopHappyPath:
    @pytest.mark.asyncio
    async def test_send_returns_skipped(self):
        svc = _service()
        delivery = await svc.send(_msg(), category=EmailCategory.SYSTEM)
        assert delivery.status == DeliveryStatus.skipped

    @pytest.mark.asyncio
    async def test_send_records_backend_name(self):
        svc = _service()
        delivery = await svc.send(_msg(), category=EmailCategory.NOTIFICATION)
        assert delivery.backend == "noop"

    @pytest.mark.asyncio
    async def test_send_records_category(self):
        svc = _service()
        delivery = await svc.send(_msg(), category=EmailCategory.AUTH)
        assert delivery.category == "auth"

    @pytest.mark.asyncio
    async def test_send_with_template_records_template(self):
        svc = _service()
        delivery = await svc.send(
            _msg(),
            category=EmailCategory.AUTH,
            template="auth/password_reset",
        )
        assert delivery.template == "auth/password_reset"


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_duplicate_key_returns_same_row(self):
        svc = _service()
        key = "idempotent-key-abc"
        first = await svc.send(_msg(), category=EmailCategory.SYSTEM, idempotency_key=key)
        second = await svc.send(_msg(), category=EmailCategory.SYSTEM, idempotency_key=key)
        assert first.id == second.id

    @pytest.mark.asyncio
    async def test_duplicate_key_does_not_resend(self):
        """The second call with the same idempotency_key must not invoke the backend again."""
        send_count = 0
        original_msg = _msg()

        class CountingNoopBackend(NoopBackend):
            async def send(self, message):
                nonlocal send_count
                send_count += 1
                return await super().send(message)

        svc = EmailService(
            backend=CountingNoopBackend(), session_factory=EmailTestSession
        )
        key = "idempotent-key-xyz"
        await svc.send(original_msg, category=EmailCategory.SYSTEM, idempotency_key=key)
        await svc.send(original_msg, category=EmailCategory.SYSTEM, idempotency_key=key)
        assert send_count == 1

    @pytest.mark.asyncio
    async def test_unique_keys_create_separate_rows(self):
        svc = _service()
        first = await svc.send(
            _msg(), category=EmailCategory.SYSTEM, idempotency_key="key-1"
        )
        second = await svc.send(
            _msg(), category=EmailCategory.SYSTEM, idempotency_key="key-2"
        )
        assert first.id != second.id
