"""Progressive lockout service (SFBL-191).

Implements two-tier account lockout on top of ``User.failed_login_count`` and
``User.locked_until``.  All mutations happen inside the caller's open
transaction — the service does NOT commit; that is the caller's responsibility.

Tier 1 — temporary auto-lock
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
If ``login_tier1_threshold`` failed login-attempt rows for the same user exist
within the last ``login_tier1_window_minutes``, set ``locked_until`` to
``now + login_tier1_lock_minutes``.  The user's ``status`` remains 'active';
the lock expires silently when the timestamp passes.  A ``login_attempt`` row
with ``outcome=tier1_auto`` is written so that the tier-2 window query can find
it.

Tier 2 — hard lock
~~~~~~~~~~~~~~~~~~~~
Triggered by either of:
  A) ``user.failed_login_count >= login_tier2_threshold``
  B) ``login_tier2_tier1_count`` or more tier-1 lock rows (``outcome=tier1_auto``)
     within ``login_tier2_window_hours``.

Effect: transition ``user.status`` from 'active' to 'locked'.  Admin unlock
required to recover.

On successful login
~~~~~~~~~~~~~~~~~~~~
Reset ``failed_login_count`` to 0 and clear ``locked_until``.

Observability
~~~~~~~~~~~~~
Every tier-1 or tier-2 lock emits:
- ``auth.account.locked`` log event with appropriate ``outcome_code``
- ``auth_account_locks_total{tier}`` metric counter
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.observability.events import AuthEvent, OutcomeCode
from app.observability.metrics import record_account_locked

_log = logging.getLogger(__name__)


# ── Public helpers ─────────────────────────────────────────────────────────────


async def handle_failed_attempt(
    db: AsyncSession,
    user: User,
    *,
    ip: str,
    username: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Process a failed authentication attempt for ``user``.

    Must be called inside an open transaction after the primary ``LoginAttempt``
    row has been flushed (so recent-failure queries include it).  The caller is
    responsible for the final ``db.commit()``.

    Steps:
    1. Increment ``failed_login_count`` and set ``last_failed_login_at``.
    2. Check tier-2 trigger A (cumulative count) first — if already at threshold,
       hard-lock immediately without a tier-1 lock.
    3. Check tier-1 threshold against recent attempt rows in the sliding window.
    4. If tier-1 triggered, set ``locked_until``, emit lock event, write a
       ``tier1_auto`` ``LoginAttempt`` row for the tier-2 window query, then
       check tier-2 trigger B (repeated tier-1 locks).
    5. If tier-2 (B) triggered after a tier-1 lock, transition ``status='locked'``.
    """
    now = datetime.now(timezone.utc)
    _username = username or (user.username or "")

    # ── 1. Increment counters ─────────────────────────────────────────────────
    user.failed_login_count = (user.failed_login_count or 0) + 1
    user.last_failed_login_at = now

    # ── 2. Tier-2 trigger A: cumulative threshold ─────────────────────────────
    if user.failed_login_count >= settings.login_tier2_threshold and user.status == "active":
        user.status = "locked"
        _log.warning(
            "Account tier-2 hard-locked (cumulative threshold)",
            extra={
                "event_name": AuthEvent.ACCOUNT_LOCKED,
                "outcome_code": OutcomeCode.TIER2_HARD,
                "user_id": user.id,
                "ip": ip,
                "cumulative_failed": user.failed_login_count,
            },
        )
        record_account_locked(OutcomeCode.TIER2_HARD)
        return

    # ── 3. Tier-1 threshold check ─────────────────────────────────────────────
    # Count failed attempts in the sliding window via login_attempt rows.
    # The primary failed-attempt row was already flushed by the caller, so the
    # query count already includes the current attempt.
    tier1_window_start = now - timedelta(minutes=settings.login_tier1_window_minutes)
    _failed_outcomes = (
        OutcomeCode.WRONG_PASSWORD,
        OutcomeCode.USER_LOCKED,
        OutcomeCode.MUST_RESET_PASSWORD,
        OutcomeCode.USER_INACTIVE,
    )
    recent_fail_count: int = await db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.user_id == user.id,
            LoginAttempt.outcome.in_(_failed_outcomes),
            LoginAttempt.attempted_at >= tier1_window_start,
        )
    ) or 0

    tier1_triggered = recent_fail_count >= settings.login_tier1_threshold

    if not tier1_triggered:
        return

    # Only (re-)trigger tier-1 if there is no active tier-1 lock yet
    already_locked = user.locked_until is not None and not _locked_until_is_past(
        user.locked_until, now
    )
    if already_locked:
        return

    # ── 4. Apply tier-1 lock ──────────────────────────────────────────────────
    user.locked_until = now + timedelta(minutes=settings.login_tier1_lock_minutes)
    _log.warning(
        "Account tier-1 auto-locked",
        extra={
            "event_name": AuthEvent.ACCOUNT_LOCKED,
            "outcome_code": OutcomeCode.TIER1_AUTO,
            "user_id": user.id,
            "locked_until": user.locked_until.isoformat(),
            "ip": ip,
            "failed_in_window": recent_fail_count,
        },
    )
    record_account_locked(OutcomeCode.TIER1_AUTO)

    # Write a tier1_auto LoginAttempt row so tier-2 window queries can count it
    tier1_row = LoginAttempt(
        id=str(uuid.uuid4()),
        user_id=user.id,
        username=_username,
        ip=ip,
        user_agent=user_agent,
        outcome=OutcomeCode.TIER1_AUTO,
        attempted_at=now,
    )
    db.add(tier1_row)
    await db.flush()

    # ── 5. Tier-2 trigger B: repeated tier-1 locks ────────────────────────────
    if user.status != "active":
        return

    tier2_window_start = now - timedelta(hours=settings.login_tier2_window_hours)
    tier1_lock_count: int = await db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.user_id == user.id,
            LoginAttempt.outcome == OutcomeCode.TIER1_AUTO,
            LoginAttempt.attempted_at >= tier2_window_start,
        )
    ) or 0

    if tier1_lock_count >= settings.login_tier2_tier1_count:
        user.status = "locked"
        _log.warning(
            "Account tier-2 hard-locked (repeated tier-1 locks)",
            extra={
                "event_name": AuthEvent.ACCOUNT_LOCKED,
                "outcome_code": OutcomeCode.TIER2_HARD,
                "user_id": user.id,
                "ip": ip,
                "tier1_lock_count": tier1_lock_count,
            },
        )
        record_account_locked(OutcomeCode.TIER2_HARD)


async def handle_successful_login(db: AsyncSession, user: User) -> None:
    """Reset lockout counters on a successful authentication.

    Clears ``failed_login_count`` and ``locked_until``.  Does NOT touch
    ``status`` — if the account was 'locked', the login gate would have blocked
    this path before reaching a successful auth.
    """
    user.failed_login_count = 0
    user.locked_until = None


# ── Private helpers ────────────────────────────────────────────────────────────


def _locked_until_is_past(locked_until: datetime, now: datetime) -> bool:
    lu = locked_until
    if lu.tzinfo is None:
        lu = lu.replace(tzinfo=timezone.utc)
    return lu <= now
