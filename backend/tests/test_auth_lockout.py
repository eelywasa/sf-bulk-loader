"""SFBL-191: Progressive lockout + admin unlock.

Tests cover:
- 5 failures in tier1_window → 6th attempt returns 401 with user_locked outcome
- Tier-1 lock auto-clears after locked_until passes (set locked_until in the past)
- 10 cumulative failures → status='locked', persisted in DB
- Admin unlock endpoint: 200 on unlock, status goes locked → active
- Non-admin caller of unlock → 403
- Self-unlock (admin calls unlock on own id) → 400
- Tier-2 trigger B: 3 tier-1 locks within 24 h → status='locked'
- Successful login after tier-1 lock expires clears failed_login_count and locked_until
- Metrics: auth_account_locks_total and auth_account_unlocks_total incremented
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Generator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.observability.events import OutcomeCode
from app.services.auth import hash_password
from tests.conftest import _TestSession, _run_async


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_user(**kwargs: Any) -> User:
    # role kwarg dropped in migration 0022 — pop and convert to is_admin.
    role = kwargs.pop("role", None)
    if role == "admin" and "is_admin" not in kwargs:
        kwargs["is_admin"] = True
    defaults = dict(
        id=str(uuid.uuid4()),
        email="testuser@example.com",
        hashed_password=hash_password("Str0ng&P4ss!"),
        status="active",
        failed_login_count=0,
    )
    defaults.update(kwargs)
    return User(**defaults)


def _make_admin(**kwargs: Any) -> User:
    # role kwarg dropped in migration 0022 — pop and convert to is_admin.
    role = kwargs.pop("role", None)
    defaults = dict(
        id=str(uuid.uuid4()),
        email="admin@example.com",
        hashed_password=hash_password("Admin&P4ss123!"),
        is_admin=True,
        status="active",
        failed_login_count=0,
    )
    defaults.update(kwargs)
    return User(**defaults)


def _seed_user(user: User) -> None:
    async def _insert() -> None:
        async with _TestSession() as session:
            session.add(user)
            await session.commit()

    _run_async(_insert())


def _get_user(user_id: str) -> User | None:
    async def _fetch() -> User | None:
        async with _TestSession() as session:
            return await session.get(User, user_id)

    return _run_async(_fetch())


def _get_attempts() -> list[LoginAttempt]:
    async def _fetch() -> list[LoginAttempt]:
        async with _TestSession() as session:
            result = await session.execute(select(LoginAttempt))
            return list(result.scalars().all())

    return _run_async(_fetch())


def _seed_login_attempts(rows: list[LoginAttempt]) -> None:
    async def _insert() -> None:
        async with _TestSession() as session:
            for row in rows:
                session.add(row)
            await session.commit()

    _run_async(_insert())


# ── Reset rate-limit store between tests ──────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_rate_limit_store():
    from app.services import rate_limit as _rl

    _rl._store.clear()
    yield
    _rl._store.clear()


# ── Tier-1 tests ───────────────────────────────────────────────────────────────


def test_tier1_lock_applied_after_threshold_failures(client):
    """5 failures in window → 6th attempt blocked with user_locked outcome."""
    user = _make_user()
    _seed_user(user)

    # Patch to threshold=5, window=15 min (matches defaults but ensures isolation)
    with _lockout_settings_patch():
        for _ in range(5):
            resp = client.post(
                "/api/auth/login",
                json={"email": "testuser@example.com", "password": "wrongpass"},
            )
            assert resp.status_code == 401

    # 6th attempt: account should now be tier-1 locked
    with _lockout_settings_patch():
        resp = client.post(
            "/api/auth/login",
            json={"email": "testuser@example.com", "password": "wrongpass"},
        )

    assert resp.status_code == 401

    # DB should have a tier1_auto attempt row
    attempts = _get_attempts()
    tier1_rows = [a for a in attempts if a.outcome == OutcomeCode.TIER1_AUTO]
    assert len(tier1_rows) >= 1

    # locked_until should be set on the user
    refreshed = _get_user(user.id)
    assert refreshed is not None
    assert refreshed.locked_until is not None
    # SQLite may store naive datetimes; normalise for comparison
    lu = refreshed.locked_until
    if lu.tzinfo is None:
        lu = lu.replace(tzinfo=timezone.utc)
    assert lu > datetime.now(timezone.utc)


def test_tier1_lock_blocks_further_logins_with_user_locked_outcome(client):
    """After tier-1 lock is set, subsequent attempt returns user_locked outcome."""
    future = datetime.now(timezone.utc) + timedelta(minutes=30)
    user = _make_user(locked_until=future)
    _seed_user(user)

    resp = client.post(
        "/api/auth/login",
        json={"email": "testuser@example.com", "password": "Str0ng&P4ss!"},
    )
    assert resp.status_code == 401

    attempts = _get_attempts()
    assert len(attempts) == 1
    assert attempts[0].outcome == OutcomeCode.USER_LOCKED


def test_tier1_lock_auto_clears_after_expiry(client):
    """Setting locked_until in the past allows login to succeed."""
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    user = _make_user(locked_until=past, failed_login_count=4)
    _seed_user(user)

    resp = client.post(
        "/api/auth/login",
        json={"email": "testuser@example.com", "password": "Str0ng&P4ss!"},
    )
    assert resp.status_code == 200, f"Expected 200 but got {resp.status_code}: {resp.json()}"

    # locked_until should be cleared, failed_login_count reset
    refreshed = _get_user(user.id)
    assert refreshed is not None
    assert refreshed.locked_until is None
    assert refreshed.failed_login_count == 0


# ── Tier-2 tests ───────────────────────────────────────────────────────────────


def test_tier2_cumulative_threshold_locks_account(client):
    """10 cumulative failures → status='locked'.

    Seed the user with failed_login_count=9 so the 10th wrong-password attempt
    (which reaches the password check and increments the counter) tips the user
    over the tier-2 threshold.  We use a high tier1_threshold so a tier-1 lock
    does not fire first and block the wrong-password path.
    """
    # Start with failed_login_count=9 so one more failure hits the threshold
    user = _make_user(failed_login_count=9)
    _seed_user(user)

    with _lockout_settings_patch(
        tier1_threshold=100,  # disable tier-1 so wrong-password path is always reached
        tier2_threshold=10,
    ):
        resp = client.post(
            "/api/auth/login",
            json={"email": "testuser@example.com", "password": "wrongpass"},
        )
        assert resp.status_code == 401

    refreshed = _get_user(user.id)
    assert refreshed is not None
    assert refreshed.status == "locked"


def test_tier2_repeated_tier1_locks_hard_locks_account(client):
    """3 tier-1 auto-lock rows within 24 h → tier-2 hard lock."""
    user = _make_user()
    _seed_user(user)

    now = datetime.now(timezone.utc)
    # Seed 2 existing tier1_auto rows in the window
    existing_tier1_rows = [
        LoginAttempt(
            id=str(uuid.uuid4()),
            user_id=user.id,
            username=user.email or "",
            ip="1.2.3.4",
            user_agent=None,
            outcome=OutcomeCode.TIER1_AUTO,
            attempted_at=now - timedelta(hours=i + 1),
        )
        for i in range(2)
    ]
    _seed_login_attempts(existing_tier1_rows)

    # Now trigger another tier-1 lock (5 failures in window)
    with _lockout_settings_patch(tier2_tier1_count=3):
        for _ in range(5):
            resp = client.post(
                "/api/auth/login",
                json={"email": "testuser@example.com", "password": "wrongpass"},
            )
            assert resp.status_code == 401

    refreshed = _get_user(user.id)
    assert refreshed is not None
    assert refreshed.status == "locked"


# ── Admin unlock tests ─────────────────────────────────────────────────────────


def test_admin_unlock_clears_tier1_lock(client):
    """Admin can unlock a tier-1 locked account."""
    admin = _make_admin()
    target = _make_user(
        id=str(uuid.uuid4()),
        email="target_user@example.com",
        locked_until=datetime.now(timezone.utc) + timedelta(minutes=30),
        failed_login_count=5,
    )
    _seed_user(admin)
    _seed_user(target)

    # Log in as admin
    login_resp = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "Admin&P4ss123!"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    # Unlock the target user
    resp = client.post(
        f"/api/admin/users/{target.id}/unlock",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "active"
    assert body["is_active"] is True

    refreshed = _get_user(target.id)
    assert refreshed is not None
    assert refreshed.locked_until is None
    assert refreshed.failed_login_count == 0


def test_admin_unlock_transitions_hard_locked_to_active(client):
    """Admin unlock transitions status from 'locked' back to 'active'."""
    admin = _make_admin()
    target = _make_user(
        id=str(uuid.uuid4()),
        email="target_user@example.com",
        status="locked",
        locked_until=datetime.now(timezone.utc) + timedelta(hours=1),
        failed_login_count=10,
    )
    _seed_user(admin)
    _seed_user(target)

    login_resp = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "Admin&P4ss123!"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    resp = client.post(
        f"/api/admin/users/{target.id}/unlock",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    # Audit row written
    attempts = _get_attempts()
    unlock_rows = [a for a in attempts if a.outcome == OutcomeCode.ADMIN_UNLOCK]
    assert len(unlock_rows) == 1
    assert unlock_rows[0].user_id == target.id


def test_non_admin_cannot_unlock(client):
    """Non-admin user calling unlock → 403."""
    regular = _make_user()
    target = _make_user(
        id=str(uuid.uuid4()),
        email="target_user@example.com",
        status="locked",
    )
    _seed_user(regular)
    _seed_user(target)

    login_resp = client.post(
        "/api/auth/login",
        json={"email": "testuser@example.com", "password": "Str0ng&P4ss!"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    resp = client.post(
        f"/api/admin/users/{target.id}/unlock",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_admin_cannot_self_unlock(client):
    """Admin calling unlock on their own id → 400."""
    admin = _make_admin()
    _seed_user(admin)

    login_resp = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "Admin&P4ss123!"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    resp = client.post(
        f"/api/admin/users/{admin.id}/unlock",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_unlock_nonexistent_user_returns_404(client):
    """Unlock on a non-existent user id → 404."""
    admin = _make_admin()
    _seed_user(admin)

    login_resp = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "Admin&P4ss123!"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    resp = client.post(
        f"/api/admin/users/{uuid.uuid4()}/unlock",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ── Metrics tests ──────────────────────────────────────────────────────────────


def test_tier1_lock_increments_metric(client):
    """Tier-1 lock event increments auth_account_locks_total{tier=tier1_auto}."""
    from app.observability.metrics import auth_account_locks_total

    user = _make_user()
    _seed_user(user)

    before = auth_account_locks_total.labels(tier=OutcomeCode.TIER1_AUTO)._value.get()

    with _lockout_settings_patch():
        for _ in range(5):
            client.post(
                "/api/auth/login",
                json={"email": "testuser@example.com", "password": "wrongpass"},
            )

    after = auth_account_locks_total.labels(tier=OutcomeCode.TIER1_AUTO)._value.get()
    assert after >= before + 1


def test_admin_unlock_increments_metric(client):
    """Admin unlock increments auth_account_unlocks_total{method=admin_manual}."""
    from app.observability.metrics import auth_account_unlocks_total

    admin = _make_admin()
    target = _make_user(
        id=str(uuid.uuid4()),
        email="target_user@example.com",
        status="locked",
    )
    _seed_user(admin)
    _seed_user(target)

    before = auth_account_unlocks_total.labels(method=OutcomeCode.ADMIN_MANUAL)._value.get()

    login_resp = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "Admin&P4ss123!"},
    )
    token = login_resp.json()["access_token"]
    client.post(
        f"/api/admin/users/{target.id}/unlock",
        headers={"Authorization": f"Bearer {token}"},
    )

    after = auth_account_unlocks_total.labels(method=OutcomeCode.ADMIN_MANUAL)._value.get()
    assert after == before + 1


# ── Successful login resets counters ───────────────────────────────────────────


def test_successful_login_resets_failed_count(client):
    """Successful login clears failed_login_count and locked_until."""
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    user = _make_user(
        locked_until=past,
        failed_login_count=3,
    )
    _seed_user(user)

    resp = client.post(
        "/api/auth/login",
        json={"email": "testuser@example.com", "password": "Str0ng&P4ss!"},
    )
    assert resp.status_code == 200

    refreshed = _get_user(user.id)
    assert refreshed is not None
    assert refreshed.failed_login_count == 0
    assert refreshed.locked_until is None


# ── Helpers ────────────────────────────────────────────────────────────────────


def _configure_lockout_settings(
    mock_auth_settings: Any,
    mock_lockout_settings: Any,
    *,
    tier1_threshold: int = 5,
    tier1_window_minutes: int = 15,
    tier1_lock_minutes: int = 30,
    tier2_threshold: int = 10,
    tier2_tier1_count: int = 3,
    tier2_window_hours: int = 24,
) -> None:
    """Configure both mock settings objects with consistent lockout values."""
    for mock in (mock_auth_settings, mock_lockout_settings):
        mock.login_rate_limit_attempts = 100  # don't interfere with rate limiting
        mock.login_rate_limit_window_seconds = 300
        mock.jwt_expiry_minutes = 60
        mock.login_tier1_threshold = tier1_threshold
        mock.login_tier1_window_minutes = tier1_window_minutes
        mock.login_tier1_lock_minutes = tier1_lock_minutes
        mock.login_tier2_threshold = tier2_threshold
        mock.login_tier2_tier1_count = tier2_tier1_count
        mock.login_tier2_window_hours = tier2_window_hours


@contextmanager
def _lockout_settings_patch(
    *,
    tier1_threshold: int = 5,
    tier1_window_minutes: int = 15,
    tier1_lock_minutes: int = 30,
    tier2_threshold: int = 10,
    tier2_tier1_count: int = 3,
    tier2_window_hours: int = 24,
) -> Generator[None, None, None]:
    """Context manager that patches both `settings` and `settings_service` for lockout tests.

    SFBL-156: lockout thresholds are now read from settings_service (DB-backed).
    This helper patches both the legacy `settings` object (for backward compat with
    existing test assertions) and `settings_service` so the new DB-backed reads see
    the correct test values.
    """
    _values: dict[str, Any] = {
        "login_rate_limit_attempts": 100,
        "login_rate_limit_window_seconds": 300,
        "jwt_expiry_minutes": 60,
        "login_tier1_threshold": tier1_threshold,
        "login_tier1_window_minutes": tier1_window_minutes,
        "login_tier1_lock_minutes": tier1_lock_minutes,
        "login_tier2_threshold": tier2_threshold,
        "login_tier2_tier1_count": tier2_tier1_count,
        "login_tier2_window_hours": tier2_window_hours,
    }
    mock_svc = AsyncMock()
    mock_svc.get = AsyncMock(side_effect=lambda key: _values.get(key))

    with (
        patch("app.api.auth.settings") as mock_auth,
        patch("app.services.auth_lockout.settings") as mock_lockout,
        patch("app.services.settings.service.settings_service", mock_svc),
    ):
        _configure_lockout_settings(mock_auth, mock_lockout,
                                    tier1_threshold=tier1_threshold,
                                    tier1_window_minutes=tier1_window_minutes,
                                    tier1_lock_minutes=tier1_lock_minutes,
                                    tier2_threshold=tier2_threshold,
                                    tier2_tier1_count=tier2_tier1_count,
                                    tier2_window_hours=tier2_window_hours)
        yield
