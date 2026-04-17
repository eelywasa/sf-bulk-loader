"""Tests for EmailService with NoopBackend.

Covers:
- Happy path returns status=skipped
- Duplicate idempotency_key returns existing row without calling backend
- Observability: email_send_total counter increments on send
"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

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


class TestObservability:
    """Verify that email_send_total increments correctly after a noop send."""

    @pytest.mark.asyncio
    async def test_send_increments_email_send_total_skipped(self):
        from app.observability.metrics import email_send_total  # noqa: F401

        svc = _service()

        def _get_count(backend, category, status):
            # metric name ends in _total so prometheus_client uses it as-is
            return REGISTRY.get_sample_value(
                "sfbl_email_send_total",
                {"backend": backend, "category": category, "status": status},
            ) or 0.0

        before = _get_count("noop", "system", "skipped")
        await svc.send(_msg(), category=EmailCategory.SYSTEM)
        after = _get_count("noop", "system", "skipped")
        assert after == before + 1.0

    @pytest.mark.asyncio
    async def test_send_does_not_increment_sent_for_noop(self):
        """Noop sends must go to 'skipped', never to 'sent'."""

        def _get_count(backend, category, status):
            return REGISTRY.get_sample_value(
                "sfbl_email_send_total",
                {"backend": backend, "category": category, "status": status},
            ) or 0.0

        svc = _service()
        before_sent = _get_count("noop", "system", "sent")
        await svc.send(_msg(), category=EmailCategory.SYSTEM)
        after_sent = _get_count("noop", "system", "sent")
        assert after_sent == before_sent  # must not have changed


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
