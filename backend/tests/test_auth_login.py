"""SFBL-190: Login endpoint observability, IP capture, per-IP rate limiting.

Tests cover:
- Successful login — token returned, login_attempt row persisted, must_reset_password=False
- Successful login with must_reset_password — flag present in response
- Wrong password — 401, wrong_password outcome persisted
- Unknown user — 401, unknown_user outcome persisted with user_id=null
- Deactivated user (status=deactivated) — 401, user_inactive outcome
- Locked user (status=locked) — 401, user_locked outcome
- Tier-1 auto-lockout (locked_until in future) — 401, user_locked outcome
- Rate-limit breach — 21st request returns 429, ip_limit outcome persisted
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.login_attempt import LoginAttempt
from app.models.user import User
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
        username="alice",
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


def _get_attempts() -> list[LoginAttempt]:
    async def _fetch():
        async with _TestSession() as session:
            result = await session.execute(select(LoginAttempt))
            return result.scalars().all()

    return _run_async(_fetch())


# ── Reset in-memory rate-limit store between tests ────────────────────────────


@pytest.fixture(autouse=True)
def reset_rate_limit_store():
    """Clear the in-memory rate-limit store before every test to avoid cross-test pollution."""
    from app.services import rate_limit as _rl

    _rl._store.clear()
    yield
    _rl._store.clear()


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_login_success_returns_token_and_persists_attempt(client):
    user = _make_user()
    _seed_user(user)
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "Str0ng&P4ss!"})

    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["must_reset_password"] is False

    attempts = _get_attempts()
    assert len(attempts) == 1
    assert attempts[0].username == "alice"
    assert attempts[0].user_id == user.id
    assert attempts[0].outcome == "ok"


def test_login_must_reset_password_flag_in_response(client):
    user = _make_user(must_reset_password=True)
    _seed_user(user)
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "Str0ng&P4ss!"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["must_reset_password"] is True

    attempts = _get_attempts()
    assert len(attempts) == 1
    assert attempts[0].outcome == "must_reset_password"


def test_login_wrong_password_returns_401(client):
    user = _make_user()
    _seed_user(user)
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "wrong!"})

    assert resp.status_code == 401

    attempts = _get_attempts()
    assert len(attempts) == 1
    assert attempts[0].outcome == "wrong_password"
    assert attempts[0].user_id == user.id
    assert attempts[0].username == "alice"


def test_login_unknown_user_returns_401(client):
    resp = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})

    assert resp.status_code == 401

    attempts = _get_attempts()
    assert len(attempts) == 1
    assert attempts[0].outcome == "unknown_user"
    assert attempts[0].user_id is None
    assert attempts[0].username == "nobody"


def test_login_deactivated_user_returns_401(client):
    user = _make_user(status="deactivated")
    _seed_user(user)
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "Str0ng&P4ss!"})

    assert resp.status_code == 401

    attempts = _get_attempts()
    assert len(attempts) == 1
    assert attempts[0].outcome == "user_inactive"
    assert attempts[0].user_id == user.id


def test_login_locked_user_returns_401(client):
    user = _make_user(status="locked")
    _seed_user(user)
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "Str0ng&P4ss!"})

    assert resp.status_code == 401

    attempts = _get_attempts()
    assert len(attempts) == 1
    assert attempts[0].outcome == "user_locked"
    assert attempts[0].user_id == user.id


def test_login_tier1_lockout_returns_401(client):
    future = datetime.now(timezone.utc) + timedelta(minutes=30)
    user = _make_user(locked_until=future)
    _seed_user(user)
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "Str0ng&P4ss!"})

    assert resp.status_code == 401

    attempts = _get_attempts()
    assert len(attempts) == 1
    assert attempts[0].outcome == "user_locked"


def test_login_rate_limit_breach_returns_429(client):
    """The 6th attempt from the same IP within the window returns HTTP 429 when limit=5."""
    user = _make_user()
    _seed_user(user)

    # Patch the DB-backed settings service to return a tight limit so the test runs fast
    _limits = {
        "login_rate_limit_attempts": 5,
        "login_rate_limit_window_seconds": 300,
        "jwt_expiry_minutes": 60,
    }
    mock_svc = AsyncMock()
    mock_svc.get = AsyncMock(side_effect=lambda key: _limits.get(key, 60))

    with patch("app.services.settings.service.settings_service", mock_svc):
        # Exhaust the limit with wrong passwords
        for _ in range(5):
            client.post("/api/auth/login", json={"username": "alice", "password": "wrong!"})

        # 6th attempt should be rate-limited
        resp = client.post("/api/auth/login", json={"username": "alice", "password": "wrong!"})

    assert resp.status_code == 429

    # The rate-limited attempt should be persisted
    attempts = _get_attempts()
    ip_limit_attempts = [a for a in attempts if a.outcome == "ip_limit"]
    assert len(ip_limit_attempts) == 1
    assert ip_limit_attempts[0].user_id is None  # rate check happens before user lookup


def test_login_rate_limit_persists_ip_limit_attempt(client):
    """An ip_limit attempt row has user_id=None and correct username."""
    user = _make_user()
    _seed_user(user)

    _limits = {
        "login_rate_limit_attempts": 1,
        "login_rate_limit_window_seconds": 300,
        "jwt_expiry_minutes": 60,
    }
    mock_svc = AsyncMock()
    mock_svc.get = AsyncMock(side_effect=lambda key: _limits.get(key, 60))

    with patch("app.services.settings.service.settings_service", mock_svc):
        # First attempt consumes the limit
        client.post("/api/auth/login", json={"username": "alice", "password": "wrong!"})
        # Second attempt is rate-limited
        resp = client.post("/api/auth/login", json={"username": "alice", "password": "x"})

    assert resp.status_code == 429

    attempts = _get_attempts()
    ip_limit = [a for a in attempts if a.outcome == "ip_limit"]
    assert len(ip_limit) == 1
    assert ip_limit[0].username == "alice"
    assert ip_limit[0].user_id is None


def test_login_persists_ip_and_user_agent(client):
    user = _make_user()
    _seed_user(user)
    resp = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "Str0ng&P4ss!"},
        headers={"User-Agent": "TestBrowser/1.0"},
    )
    assert resp.status_code == 200

    attempts = _get_attempts()
    assert len(attempts) == 1
    # In TestClient the IP is typically 'testclient' or similar
    assert attempts[0].ip is not None
    assert attempts[0].user_agent == "TestBrowser/1.0"


def test_login_emits_structured_log_on_success(client, caplog):
    import logging

    user = _make_user()
    _seed_user(user)

    with caplog.at_level(logging.INFO, logger="app.api.auth"):
        resp = client.post("/api/auth/login", json={"username": "alice", "password": "Str0ng&P4ss!"})

    assert resp.status_code == 200
    # At least one log record should have been emitted during the request
    assert len(caplog.records) > 0


def test_login_metric_incremented_on_success(client):
    from app.observability.metrics import auth_login_attempts_total

    user = _make_user()
    _seed_user(user)

    before = auth_login_attempts_total.labels(outcome="ok")._value.get()
    client.post("/api/auth/login", json={"username": "alice", "password": "Str0ng&P4ss!"})
    after = auth_login_attempts_total.labels(outcome="ok")._value.get()

    assert after == before + 1


def test_login_metric_incremented_on_wrong_password(client):
    from app.observability.metrics import auth_login_attempts_total

    user = _make_user()
    _seed_user(user)

    before = auth_login_attempts_total.labels(outcome="wrong_password")._value.get()
    client.post("/api/auth/login", json={"username": "alice", "password": "bad"})
    after = auth_login_attempts_total.labels(outcome="wrong_password")._value.get()

    assert after == before + 1
