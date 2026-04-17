"""Tests for EmailService retry behaviour.

Covers:
- Transient failure schedules a retry task; row eventually reaches terminal state
- Two concurrent _retry_loop tasks on the same row: exactly one claim wins
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.models.email_delivery import DeliveryStatus
from app.services.email import delivery_log
from app.services.email.backends.base import BackendResult
from app.services.email.errors import EmailErrorReason
from app.services.email.message import EmailCategory, EmailMessage
from app.services.email.service import EmailService
from tests.services.email.conftest import EmailTestSession


def _msg(to: str = "retry@example.com") -> EmailMessage:
    return EmailMessage(to=to, subject="Retry Test", text_body="retry test body")


class FailNTimesBackend:
    """Backend that fails `fail_count` times then succeeds."""

    name = "fake_transient"

    def __init__(self, fail_count: int = 1):
        self.fail_count = fail_count
        self.call_count = 0

    async def send(self, message: EmailMessage) -> BackendResult:
        self.call_count += 1
        if self.call_count <= self.fail_count:
            return BackendResult(
                accepted=False,
                provider_message_id=None,
                reason=EmailErrorReason.TRANSIENT_NETWORK,
                error_detail="[FAKE] connection refused",
                transient=True,
            )
        return BackendResult(
            accepted=True,
            provider_message_id="fake-msg-id",
            reason=None,
            error_detail=None,
            transient=False,
        )

    async def healthcheck(self) -> bool:
        return True

    def classify(self, exc_or_code: Any):
        return (EmailErrorReason.TRANSIENT_NETWORK, True)


class PermanentFailBackend:
    """Backend that always fails permanently."""

    name = "fake_permanent"

    async def send(self, message: EmailMessage) -> BackendResult:
        return BackendResult(
            accepted=False,
            provider_message_id=None,
            reason=EmailErrorReason.PERMANENT_REJECT,
            error_detail="[FAKE:550] rejected",
            transient=False,
        )

    async def healthcheck(self) -> bool:
        return True

    def classify(self, exc_or_code: Any):
        return (EmailErrorReason.PERMANENT_REJECT, False)


class TestTransientRetry:
    @pytest.mark.asyncio
    async def test_transient_failure_schedules_retry_and_reaches_sent(self):
        """A single transient failure should recover via _retry_loop.

        We test the full retry path by:
        1. Doing the first (failing) attempt via send() with sleep mocked.
        2. Manually setting next_attempt_at to the past so the scheduled
           retry task can claim the row immediately.
        3. Awaiting the scheduled retry task and verifying terminal state.
        """
        from datetime import timedelta
        from sqlalchemy import update, select

        from app.models.email_delivery import EmailDelivery

        backend = FailNTimesBackend(fail_count=1)
        svc = EmailService(backend=backend, session_factory=EmailTestSession)

        # Collect the tasks that exist before the send so we can find the
        # new retry task that send() creates.
        tasks_before = set(asyncio.all_tasks())

        # Patch asyncio.sleep so the retry fires immediately
        with patch("app.services.email.service.asyncio.sleep", new_callable=AsyncMock):
            delivery = await svc.send(_msg(), category=EmailCategory.NOTIFICATION)

        # After the first attempt the row should be pending
        assert delivery.status == DeliveryStatus.pending
        delivery_id = delivery.id

        # Find the retry task that send() created
        retry_tasks = [
            t for t in asyncio.all_tasks()
            if t not in tasks_before and "email_retry_" in (t.get_name() or "")
        ]
        assert len(retry_tasks) == 1, f"Expected 1 retry task, got {len(retry_tasks)}"
        retry_task = retry_tasks[0]

        # Move next_attempt_at into the past so the claim condition passes
        async with EmailTestSession() as s:
            past = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
            await s.execute(
                update(EmailDelivery)
                .where(EmailDelivery.id == delivery_id)
                .values(next_attempt_at=past)
                .execution_options(synchronize_session=False)
            )
            await s.commit()

        # Allow the scheduled retry task to run (sleep still mocked to instant)
        with patch("app.services.email.service.asyncio.sleep", new_callable=AsyncMock):
            await retry_task

        # Re-fetch and confirm the row reached 'sent'
        async with EmailTestSession() as s:
            result = await s.execute(
                select(EmailDelivery).where(EmailDelivery.id == delivery_id)
            )
            updated = result.scalar_one()
        assert updated.status == DeliveryStatus.sent
        assert backend.call_count == 2

    @pytest.mark.asyncio
    async def test_permanent_failure_does_not_retry(self):
        """Permanent failures go directly to failed without scheduling retry."""
        backend = PermanentFailBackend()
        svc = EmailService(backend=backend, session_factory=EmailTestSession)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            delivery = await svc.send(_msg(), category=EmailCategory.NOTIFICATION)

        assert delivery.status == DeliveryStatus.failed
        assert delivery.last_error_code == "permanent_reject"

    @pytest.mark.asyncio
    async def test_exhausted_retries_becomes_failed(self):
        """When max_attempts is reached, transient failure is terminal."""
        from app.config import settings

        # Set max_retries to 0 so the first attempt is the only one
        original = settings.email_max_retries
        settings.email_max_retries = 0
        try:
            backend = FailNTimesBackend(fail_count=99)
            svc = EmailService(backend=backend, session_factory=EmailTestSession)
            delivery = await svc.send(_msg(), category=EmailCategory.NOTIFICATION)
        finally:
            settings.email_max_retries = original

        assert delivery.status == DeliveryStatus.failed


class TestConcurrentRetryLoopClaim:
    @pytest.mark.asyncio
    async def test_exactly_one_claim_wins(self, caplog):
        """Two concurrent _retry_loop tasks on the same row — exactly one succeeds."""
        # Create a row in pending state with an expired lease (so it's claimable)
        from datetime import timedelta
        from sqlalchemy import update

        from app.models.email_delivery import EmailDelivery

        backend = FailNTimesBackend(fail_count=0)  # succeeds immediately
        svc = EmailService(backend=backend, session_factory=EmailTestSession)

        # Insert a row manually in pending/retrying state
        msg = _msg()
        async with EmailTestSession() as s:
            row = await delivery_log.insert(
                s, msg=msg, backend_name="fake_transient", category=EmailCategory.SYSTEM
            )
            # Expire the lease so both tasks can attempt to claim it
            past = datetime.now(tz=timezone.utc) - timedelta(seconds=120)
            await s.execute(
                update(EmailDelivery)
                .where(EmailDelivery.id == row.id)
                .values(
                    claim_expires_at=past,
                    next_attempt_at=past,
                    status=DeliveryStatus.pending,
                )
            )
            await s.commit()

        claim_lost_count = 0
        original_log_info = logging.Logger.info

        def _count_claim_lost(self_logger, msg, *args, **kwargs):
            nonlocal claim_lost_count
            extra = kwargs.get("extra", {})
            if extra.get("event_name") == "email.send.claim_lost":
                claim_lost_count += 1
            original_log_info(self_logger, msg, *args, **kwargs)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with patch.object(logging.Logger, "info", _count_claim_lost):
                results = await asyncio.gather(
                    svc._retry_loop(row.id, msg),
                    svc._retry_loop(row.id, msg),
                )

        # One task should have logged claim_lost; the other succeeded
        assert claim_lost_count == 1
