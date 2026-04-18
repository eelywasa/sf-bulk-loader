"""Delivery log persistence helpers for the email_delivery table.

All database interactions relating to the email_delivery lifecycle live here.
`EmailService` calls these helpers; callers outside the email package should
not write directly to email_delivery.

CAS claim design
----------------
The `claim()` helper uses a SQLAlchemy Core UPDATE with RETURNING to atomically
grab a lease on a pending row.  SQLite >= 3.35 supports RETURNING, and the
project uses aiosqlite, so this is safe on both SQLite and PostgreSQL.

If RETURNING ever causes a dialect issue (e.g. older SQLite), fall back to a
fetch-then-update-then-re-fetch pattern — but the current approach is
preferred because it eliminates the window between the fetch and the update.

Worker identity
---------------
WORKER_ID is computed once at module import and written to claimed_by on
every claim operation.  It identifies which process holds a lease, enabling
operators to correlate stale claims with specific service instances.
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.email_delivery import DeliveryStatus, EmailDelivery
from app.observability.sanitization import safe_exc_message
from app.services.email.errors import EmailErrorReason
from app.services.email.message import EmailCategory, EmailMessage

logger = logging.getLogger(__name__)

# Stable worker identity for CAS lease claims.
# Format: "{hostname}:{pid}" — written to email_delivery.claimed_by.
WORKER_ID: str = f"{socket.gethostname()}:{os.getpid()}"


def _compute_to_hash(addr: str) -> str:
    """Return sha256 hex digest of the lowercased address."""
    return hashlib.sha256(addr.lower().encode()).hexdigest()


def _extract_domain(addr: str) -> str:
    """Return the domain part of an email address.

    Assumes addr has already been validated (contains exactly one '@').
    """
    return addr.split("@", 1)[1]


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


async def insert(
    session: AsyncSession,
    *,
    msg: EmailMessage,
    backend_name: str,
    category: EmailCategory,
    template: str | None = None,
    idempotency_key: str | None = None,
) -> EmailDelivery:
    """Insert a new email_delivery row in 'pending' status with an active lease.

    The insert already claims the row (claimed_by=WORKER_ID, claim_expires_at
    = now + EMAIL_CLAIM_LEASE_SECONDS) so no other worker can pre-empt the
    first attempt.
    """
    import email.utils

    now = _now_utc()
    lease_expires = datetime.fromtimestamp(
        now.timestamp() + settings.email_claim_lease_seconds,
        tz=timezone.utc,
    )

    # Resolve the bare addr_spec from the (possibly display-name) address
    _, addr_spec = email.utils.parseaddr(msg.to)
    addr_for_hash = addr_spec if addr_spec else msg.to

    to_addr_value: str | None = addr_spec if settings.email_log_recipients else None

    delivery = EmailDelivery(
        created_at=now,
        updated_at=now,
        category=category.value,
        template=template,
        backend=backend_name,
        to_hash=_compute_to_hash(addr_for_hash),
        to_domain=_extract_domain(addr_for_hash),
        to_addr=to_addr_value,
        subject=msg.subject,
        status=DeliveryStatus.pending,
        attempts=0,
        # Snapshot retry budget so per-row limits survive config changes mid-flight
        max_attempts=settings.email_max_retries + 1,
        idempotency_key=idempotency_key,
        claimed_by=WORKER_ID,
        claim_expires_at=lease_expires,
        next_attempt_at=now,
    )
    session.add(delivery)
    await session.commit()
    await session.refresh(delivery)
    return delivery


async def get_by_idempotency_key(
    session: AsyncSession, idempotency_key: str
) -> EmailDelivery | None:
    """Return an existing row with the given idempotency_key, or None."""
    from sqlalchemy import select

    result = await session.execute(
        select(EmailDelivery).where(EmailDelivery.idempotency_key == idempotency_key)
    )
    return result.scalar_one_or_none()


async def claim(
    session: AsyncSession,
    delivery_id: str,
    worker_id: str,
    lease_seconds: int,
) -> EmailDelivery | None:
    """Attempt a CAS claim on a pending row.

    Returns the refreshed EmailDelivery row if this worker won the claim,
    or None if the claim was lost (another worker holds a live lease, or the
    row is already in a terminal state).

    Uses UPDATE ... RETURNING for an atomic claim.  SQLite >= 3.35 supports
    RETURNING, which is required by the project's aiosqlite configuration.
    """
    now = _now_utc()
    new_expires = datetime.fromtimestamp(
        now.timestamp() + lease_seconds,
        tz=timezone.utc,
    )

    stmt = (
        update(EmailDelivery)
        .where(
            EmailDelivery.id == delivery_id,
            EmailDelivery.status.in_(
                [DeliveryStatus.pending, DeliveryStatus.sending]
            ),
            # Claim is available if no lease or lease has expired
            (EmailDelivery.claim_expires_at.is_(None))
            | (EmailDelivery.claim_expires_at < now),
            # Retry is due
            (EmailDelivery.next_attempt_at.is_(None))
            | (EmailDelivery.next_attempt_at <= now),
        )
        .values(
            claimed_by=worker_id,
            claim_expires_at=new_expires,
            status=DeliveryStatus.sending,
            updated_at=now,
        )
        .returning(EmailDelivery.id)
        # Disable ORM session sync evaluation — the WHERE clause uses
        # timezone-aware datetimes which the ORM evaluator can't compare
        # in-memory.  The DB handles the WHERE correctly.
        .execution_options(synchronize_session=False)
    )

    result = await session.execute(stmt)
    claimed_id = result.scalar_one_or_none()
    await session.commit()

    if claimed_id is None:
        return None

    # Re-fetch the full row after commit
    from sqlalchemy import select

    row = await session.execute(
        select(EmailDelivery).where(EmailDelivery.id == claimed_id)
    )
    return row.scalar_one_or_none()


async def mark_sent(
    session: AsyncSession,
    delivery: EmailDelivery,
    provider_message_id: str | None,
) -> None:
    """Mark a delivery row as successfully sent."""
    now = _now_utc()
    delivery.status = DeliveryStatus.sent
    delivery.sent_at = now
    delivery.provider_message_id = provider_message_id
    delivery.claim_expires_at = None  # release lease (NULL = immediately claimable)
    delivery.claimed_by = None
    delivery.updated_at = now
    await session.commit()
    await session.refresh(delivery)


async def mark_failed(
    session: AsyncSession,
    delivery: EmailDelivery,
    reason: EmailErrorReason,
    error_detail: str | None,
    *,
    next_attempt_at: datetime | None = None,
) -> None:
    """Mark a delivery row as failed (permanently or pending-for-retry).

    If `next_attempt_at` is provided the row stays in 'pending' status (retry
    will be attempted).  Otherwise status is set to 'failed' (terminal).
    """
    now = _now_utc()
    delivery.attempts += 1
    delivery.last_error_code = reason.value
    delivery.last_error_msg = error_detail
    delivery.claim_expires_at = None  # release lease (NULL = immediately claimable)
    delivery.claimed_by = None
    delivery.updated_at = now

    if next_attempt_at is not None:
        delivery.status = DeliveryStatus.pending
        delivery.next_attempt_at = next_attempt_at
    else:
        delivery.status = DeliveryStatus.failed
        delivery.next_attempt_at = None

    await session.commit()
    await session.refresh(delivery)


async def mark_skipped(
    session: AsyncSession,
    delivery: EmailDelivery,
) -> None:
    """Mark a noop delivery as skipped (terminal state for NoopBackend)."""
    now = _now_utc()
    delivery.status = DeliveryStatus.skipped
    delivery.sent_at = now
    delivery.claim_expires_at = None  # release lease (NULL = immediately claimable)
    delivery.claimed_by = None
    delivery.updated_at = now
    await session.commit()
    await session.refresh(delivery)


async def boot_sweep(session: AsyncSession, stale_minutes: int) -> int:
    """Reap stale pending/sending rows whose lease has been expired for > stale_minutes.

    Rows where `claim_expires_at < now - stale_minutes` are assumed abandoned
    (e.g. process crashed mid-send) and are moved to 'failed'.

    Returns the number of rows reaped.
    """
    now = _now_utc()
    # Threshold: leases expired more than stale_minutes ago
    stale_threshold = datetime.fromtimestamp(
        now.timestamp() - stale_minutes * 60,
        tz=timezone.utc,
    )

    stmt = (
        update(EmailDelivery)
        .where(
            EmailDelivery.status.in_(
                [DeliveryStatus.pending, DeliveryStatus.sending]
            ),
            # Either no lease set but still pending/sending (unusual), or lease
            # expired more than stale_minutes ago
            (EmailDelivery.claim_expires_at.is_(None))
            | (EmailDelivery.claim_expires_at < stale_threshold),
        )
        .values(
            status=DeliveryStatus.failed,
            last_error_code=EmailErrorReason.UNKNOWN.value,
            last_error_msg="[SWEEP] reaped stale claim",
            claimed_by=None,
            claim_expires_at=None,
            updated_at=now,
        )
        .returning(EmailDelivery.id)
        # Disable ORM session sync evaluation — the WHERE clause uses
        # timezone-aware datetimes which the ORM evaluator can't compare
        # in-memory.  The DB handles the WHERE correctly.
        .execution_options(synchronize_session=False)
    )

    result = await session.execute(stmt)
    reaped_ids = result.fetchall()
    reaped_count = len(reaped_ids)
    await session.commit()
    return reaped_count
