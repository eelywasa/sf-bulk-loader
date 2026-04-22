"""SFBL-192: GET /api/me/login-history — recent sign-in activity.

Tests cover:
- 401 returned when not authenticated
- Returns only the current user's rows (other users' rows excluded)
- Outcome is masked: 'ok' → 'Success', everything else → 'Failed'
- Respects `limit` query param (default 10, clamped to 1–50)
- Excludes unknown-user rows (user_id=None)
- Returns most-recent first
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete

from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.observability.events import OutcomeCode
from app.services.auth import hash_password
from tests.conftest import _TestSession, _run_async


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_user(**kwargs) -> User:
    # role kwarg dropped in migration 0022 — pop and convert to is_admin.
    role = kwargs.pop("role", None)
    if role == "admin" and "is_admin" not in kwargs:
        kwargs["is_admin"] = True
    defaults = dict(
        id=str(uuid.uuid4()),
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("Str0ng&P4ss!"),
        status="active",
    )
    defaults.update(kwargs)
    return User(**defaults)


def _seed_user(user: User) -> None:
    async def _insert():
        async with _TestSession() as session:
            session.add(user)
            await session.commit()

    _run_async(_insert())


def _seed_attempt(
    *,
    user_id: str | None,
    outcome: str,
    ip: str = "1.2.3.4",
    attempted_at: datetime | None = None,
) -> LoginAttempt:
    row = LoginAttempt(
        id=str(uuid.uuid4()),
        user_id=user_id,
        username="someone",
        ip=ip,
        outcome=outcome,
        attempted_at=attempted_at or datetime.now(timezone.utc),
    )

    async def _insert():
        async with _TestSession() as session:
            session.add(row)
            await session.commit()

    _run_async(_insert())
    return row


def _login_and_token(client, user: User, password: str = "Str0ng&P4ss!") -> str:
    """Login via the real login endpoint and return a bearer token."""
    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# ── Reset rate-limit store to prevent cross-test pollution ────────────────────


@pytest.fixture(autouse=True)
def reset_rate_limit_store():
    from app.services import rate_limit as _rl

    _rl._store.clear()
    yield
    _rl._store.clear()


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_login_history_requires_auth(client):
    """401 when no Authorization header is sent."""
    resp = client.get("/api/me/login-history")
    assert resp.status_code == 401


def test_login_history_returns_only_current_user_rows(client):
    """Only rows for the authenticated user are returned; other users' rows excluded."""
    user_a = _make_user(email="alice-hist@example.com")
    user_b = _make_user(email="bob-hist@example.com")
    _seed_user(user_a)
    _seed_user(user_b)

    # Seed one attempt for A and one for B
    _seed_attempt(user_id=user_a.id, outcome=OutcomeCode.OK)
    _seed_attempt(user_id=user_b.id, outcome=OutcomeCode.WRONG_PASSWORD)

    token = _login_and_token(client, user_a)
    resp = client.get(
        "/api/me/login-history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    rows = resp.json()
    # Should include A's pre-seeded attempt + the successful login attempt
    user_ids_in_response = {r["ip"] for r in rows}
    # The response must not contain anything from B — verify by checking that
    # all returned rows have the correct outcome label (B's row is 'Failed')
    # and that only 2 rows come back (seed + login).
    assert all(r["outcome"] in ("Success", "Failed") for r in rows)
    # B's row must NOT appear — B's row ip is 1.2.3.4 with outcome wrong_password.
    # Since A's row is also 1.2.3.4 with outcome 'ok', check count: should be 2
    # (the seed attempt for A + the login that fetched the token).
    assert len(rows) == 2


def test_login_history_masks_outcomes(client):
    """'ok' maps to 'Success'; everything else maps to 'Failed'."""
    user = _make_user(email="carol-hist@example.com")
    _seed_user(user)

    _seed_attempt(user_id=user.id, outcome=OutcomeCode.OK, ip="10.0.0.1")
    _seed_attempt(user_id=user.id, outcome=OutcomeCode.WRONG_PASSWORD, ip="10.0.0.2")
    _seed_attempt(user_id=user.id, outcome=OutcomeCode.TIER1_AUTO, ip="10.0.0.3")
    _seed_attempt(user_id=user.id, outcome=OutcomeCode.TIER2_HARD, ip="10.0.0.4")

    token = _login_and_token(client, user)
    resp = client.get(
        "/api/me/login-history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    rows = resp.json()
    by_ip = {r["ip"]: r["outcome"] for r in rows}

    assert by_ip["10.0.0.1"] == "Success"
    assert by_ip["10.0.0.2"] == "Failed"
    assert by_ip["10.0.0.3"] == "Failed"
    assert by_ip["10.0.0.4"] == "Failed"
    # The login that fetched the token is also Success
    # (testclient uses 127.0.0.1 or testclient ip)
    assert all(v in ("Success", "Failed") for v in by_ip.values())


def test_login_history_default_limit(client):
    """Default limit is 10 — seeding 15 rows returns at most 10."""
    user = _make_user(email="dave-hist@example.com")
    _seed_user(user)

    for i in range(15):
        _seed_attempt(user_id=user.id, outcome=OutcomeCode.WRONG_PASSWORD, ip=f"10.1.0.{i}")

    token = _login_and_token(client, user)
    resp = client.get(
        "/api/me/login-history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 10


def test_login_history_limit_param_respected(client):
    """?limit=3 returns at most 3 rows."""
    user = _make_user(email="eve-hist@example.com")
    _seed_user(user)

    for i in range(5):
        _seed_attempt(user_id=user.id, outcome=OutcomeCode.WRONG_PASSWORD, ip=f"10.2.0.{i}")

    token = _login_and_token(client, user)
    resp = client.get(
        "/api/me/login-history?limit=3",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) <= 3


def test_login_history_limit_clamped_to_50(client):
    """?limit=999 is rejected with 422 (exceeds max 50)."""
    user = _make_user(email="frank-hist@example.com")
    _seed_user(user)
    token = _login_and_token(client, user)
    resp = client.get(
        "/api/me/login-history?limit=999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_login_history_limit_below_1_rejected(client):
    """?limit=0 is rejected with 422 (below min 1)."""
    user = _make_user(email="grace-hist@example.com")
    _seed_user(user)
    token = _login_and_token(client, user)
    resp = client.get(
        "/api/me/login-history?limit=0",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_login_history_excludes_null_user_id_rows(client):
    """Unknown-user rows (user_id=None) are excluded from the response."""
    user = _make_user(email="helen-hist@example.com")
    _seed_user(user)

    # Seed an unknown-user attempt
    _seed_attempt(user_id=None, outcome=OutcomeCode.UNKNOWN_USER, ip="99.99.99.99")

    token = _login_and_token(client, user)
    resp = client.get(
        "/api/me/login-history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    rows = resp.json()
    ips = [r["ip"] for r in rows]
    assert "99.99.99.99" not in ips


def test_login_history_ordered_newest_first(client):
    """Results are returned newest-first (descending attempted_at)."""
    from datetime import timedelta

    user = _make_user(email="ivan-hist@example.com")
    _seed_user(user)

    base = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    _seed_attempt(user_id=user.id, outcome=OutcomeCode.OK, ip="192.168.1.1", attempted_at=base)
    _seed_attempt(
        user_id=user.id, outcome=OutcomeCode.WRONG_PASSWORD, ip="192.168.1.2",
        attempted_at=base + timedelta(minutes=5),
    )
    _seed_attempt(
        user_id=user.id, outcome=OutcomeCode.OK, ip="192.168.1.3",
        attempted_at=base + timedelta(minutes=10),
    )

    token = _login_and_token(client, user)
    resp = client.get(
        "/api/me/login-history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    rows = resp.json()
    # Parse attempted_at and verify descending order
    times = [datetime.fromisoformat(r["attempted_at"].replace("Z", "+00:00")) for r in rows]
    assert times == sorted(times, reverse=True)
