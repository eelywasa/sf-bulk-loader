"""Tests for delivery_log helpers.

Covers:
- insert populates to_hash, to_domain correctly
- to_addr is null unless EMAIL_LOG_RECIPIENTS=true
- CAS claim succeeds once; second concurrent claim on same row returns None
- boot_sweep reaps expired claims; leaves fresh claims untouched
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio

from app.models.email_delivery import DeliveryStatus, EmailDelivery
from app.services.email import delivery_log
from app.services.email.message import EmailCategory, EmailMessage


def _msg(to: str = "alice@example.com") -> EmailMessage:
    return EmailMessage(to=to, subject="Test", text_body="Hello")


class TestInsert:
    @pytest.mark.asyncio
    async def test_hash_and_domain(self, session):
        msg = _msg("Alice@Example.COM")
        row = await delivery_log.insert(
            session,
            msg=msg,
            backend_name="noop",
            category=EmailCategory.SYSTEM,
        )
        expected_hash = hashlib.sha256("alice@example.com".encode()).hexdigest()
        assert row.to_hash == expected_hash
        assert row.to_domain == "Example.COM"

    @pytest.mark.asyncio
    async def test_to_addr_none_by_default(self, session):
        from app.config import settings

        original = settings.email_log_recipients
        settings.email_log_recipients = False
        try:
            row = await delivery_log.insert(
                session,
                msg=_msg(),
                backend_name="noop",
                category=EmailCategory.SYSTEM,
            )
        finally:
            settings.email_log_recipients = original
        assert row.to_addr is None

    @pytest.mark.asyncio
    async def test_to_addr_populated_when_opted_in(self, session):
        from app.config import settings

        original = settings.email_log_recipients
        settings.email_log_recipients = True
        try:
            row = await delivery_log.insert(
                session,
                msg=_msg("bob@test.org"),
                backend_name="noop",
                category=EmailCategory.AUTH,
            )
        finally:
            settings.email_log_recipients = original
        assert row.to_addr == "bob@test.org"

    @pytest.mark.asyncio
    async def test_status_pending_on_insert(self, session):
        row = await delivery_log.insert(
            session,
            msg=_msg(),
            backend_name="noop",
            category=EmailCategory.NOTIFICATION,
        )
        assert row.status == DeliveryStatus.pending

    @pytest.mark.asyncio
    async def test_max_attempts_snapshot(self, session):
        from app.config import settings

        row = await delivery_log.insert(
            session,
            msg=_msg(),
            backend_name="noop",
            category=EmailCategory.SYSTEM,
        )
        assert row.max_attempts == settings.email_max_retries + 1

    @pytest.mark.asyncio
    async def test_idempotency_key_stored(self, session):
        row = await delivery_log.insert(
            session,
            msg=_msg(),
            backend_name="noop",
            category=EmailCategory.SYSTEM,
            idempotency_key="unique-key-123",
        )
        assert row.idempotency_key == "unique-key-123"

    @pytest.mark.asyncio
    async def test_claimed_by_set_on_insert(self, session):
        row = await delivery_log.insert(
            session,
            msg=_msg(),
            backend_name="noop",
            category=EmailCategory.SYSTEM,
        )
        assert row.claimed_by == delivery_log.WORKER_ID
        assert row.claim_expires_at is not None


class TestClaim:
    @pytest.mark.asyncio
    async def test_claim_succeeds_on_available_row(self, session):
        row = await delivery_log.insert(
            session, msg=_msg(), backend_name="noop", category=EmailCategory.SYSTEM
        )
        row_id = row.id
        # First, release the lease so the row is claimable
        from sqlalchemy import update

        past = datetime.now(tz=timezone.utc) - timedelta(seconds=120)
        await session.execute(
            update(EmailDelivery)
            .where(EmailDelivery.id == row_id)
            .values(
                claim_expires_at=past,
                status=DeliveryStatus.pending,
                next_attempt_at=past,
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()

        # Use a fresh session for the claim (matches production pattern)
        from tests.services.email.conftest import EmailTestSession

        async with EmailTestSession() as fresh_session:
            claimed = await delivery_log.claim(
                fresh_session, row_id, delivery_log.WORKER_ID, 60
            )
        assert claimed is not None
        assert claimed.claimed_by == delivery_log.WORKER_ID
        assert claimed.status == DeliveryStatus.sending

    @pytest.mark.asyncio
    async def test_claim_fails_while_lease_active(self, session):
        """Two concurrent claim() calls on the same row — only one succeeds."""
        row = await delivery_log.insert(
            session, msg=_msg(), backend_name="noop", category=EmailCategory.SYSTEM
        )
        # Make row claimable by expiring the insert-time lease
        from sqlalchemy import update

        past = datetime.now(tz=timezone.utc) - timedelta(seconds=120)
        await session.execute(
            update(EmailDelivery)
            .where(EmailDelivery.id == row.id)
            .values(
                claim_expires_at=past,
                status=DeliveryStatus.pending,
                next_attempt_at=past,
            )
        )
        await session.commit()

        # Use two separate sessions to simulate concurrent claims
        from tests.services.email.conftest import EmailTestSession

        async def _claim(worker_id: str) -> bool:
            async with EmailTestSession() as s:
                result = await delivery_log.claim(s, row.id, worker_id, 60)
                return result is not None

        results = await asyncio.gather(
            _claim("worker-A"),
            _claim("worker-B"),
        )
        # Exactly one claim should succeed
        assert sum(results) == 1

    @pytest.mark.asyncio
    async def test_claim_fails_on_terminal_row(self, session):
        row = await delivery_log.insert(
            session, msg=_msg(), backend_name="noop", category=EmailCategory.SYSTEM
        )
        await delivery_log.mark_skipped(session, row)

        claimed = await delivery_log.claim(
            session, row.id, delivery_log.WORKER_ID, 60
        )
        assert claimed is None


class TestMarkHelpers:
    @pytest.mark.asyncio
    async def test_mark_sent(self, session):
        row = await delivery_log.insert(
            session, msg=_msg(), backend_name="smtp", category=EmailCategory.NOTIFICATION
        )
        await delivery_log.mark_sent(session, row, "msg-id-123")
        assert row.status == DeliveryStatus.sent
        assert row.provider_message_id == "msg-id-123"
        assert row.claimed_by is None

    @pytest.mark.asyncio
    async def test_mark_skipped(self, session):
        row = await delivery_log.insert(
            session, msg=_msg(), backend_name="noop", category=EmailCategory.SYSTEM
        )
        await delivery_log.mark_skipped(session, row)
        assert row.status == DeliveryStatus.skipped
        assert row.claimed_by is None

    @pytest.mark.asyncio
    async def test_mark_failed_terminal(self, session):
        from app.services.email.errors import EmailErrorReason

        row = await delivery_log.insert(
            session, msg=_msg(), backend_name="smtp", category=EmailCategory.AUTH
        )
        await delivery_log.mark_failed(
            session, row, EmailErrorReason.PERMANENT_REJECT, "[SMTP:550] rejected"
        )
        assert row.status == DeliveryStatus.failed
        assert row.last_error_code == "permanent_reject"
        assert row.claimed_by is None

    @pytest.mark.asyncio
    async def test_mark_failed_with_next_attempt_keeps_pending(self, session):
        from app.services.email.errors import EmailErrorReason

        row = await delivery_log.insert(
            session, msg=_msg(), backend_name="smtp", category=EmailCategory.NOTIFICATION
        )
        future = datetime.now(tz=timezone.utc) + timedelta(seconds=30)
        await delivery_log.mark_failed(
            session,
            row,
            EmailErrorReason.TRANSIENT_NETWORK,
            "connection refused",
            next_attempt_at=future,
        )
        assert row.status == DeliveryStatus.pending
        assert row.next_attempt_at is not None
        assert row.attempts == 1


class TestBootSweep:
    @pytest.mark.asyncio
    async def test_reaps_expired_claims(self, session):
        """Rows with claim_expires_at far in the past are moved to failed."""
        row = await delivery_log.insert(
            session, msg=_msg(), backend_name="noop", category=EmailCategory.SYSTEM
        )
        # Force claim_expires_at to be very stale
        from sqlalchemy import update

        stale_time = datetime.now(tz=timezone.utc) - timedelta(minutes=30)
        await session.execute(
            update(EmailDelivery)
            .where(EmailDelivery.id == row.id)
            .values(claim_expires_at=stale_time, status=DeliveryStatus.pending)
        )
        await session.commit()

        reaped = await delivery_log.boot_sweep(session, stale_minutes=15)
        assert reaped == 1

        await session.refresh(row)
        assert row.status == DeliveryStatus.failed
        assert row.last_error_code == "unknown"
        assert "[SWEEP]" in (row.last_error_msg or "")

    @pytest.mark.asyncio
    async def test_does_not_reap_fresh_claims(self, session):
        """Rows with an active lease are left untouched."""
        row = await delivery_log.insert(
            session, msg=_msg(), backend_name="noop", category=EmailCategory.SYSTEM
        )
        # claim_expires_at is set to now + lease_seconds by insert — it's fresh
        reaped = await delivery_log.boot_sweep(session, stale_minutes=15)
        assert reaped == 0

        await session.refresh(row)
        assert row.status == DeliveryStatus.pending

    @pytest.mark.asyncio
    async def test_does_not_reap_terminal_rows(self, session):
        row = await delivery_log.insert(
            session, msg=_msg(), backend_name="noop", category=EmailCategory.SYSTEM
        )
        await delivery_log.mark_skipped(session, row)

        reaped = await delivery_log.boot_sweep(session, stale_minutes=0)
        assert reaped == 0
