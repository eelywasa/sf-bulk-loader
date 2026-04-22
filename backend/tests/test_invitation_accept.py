"""Tests for GET/POST /api/invitations/* endpoints (SFBL-202).

Coverage
--------
- GET  /api/invitations/{token}         — valid token returns user info
- GET  /api/invitations/{token}         — expired token → 404
- GET  /api/invitations/{token}         — used token → 410
- GET  /api/invitations/{token}         — invalid (unknown) token → 404
- POST /api/invitations/{token}/accept  — happy path: password set, JWT returned, user active
- POST /api/invitations/{token}/accept  — password policy failure → 422
- POST /api/invitations/{token}/accept  — expired token → 410
- POST /api/invitations/{token}/accept  — already-used token → 410
- POST /api/invitations/{token}/accept  — invalid token → 410
- Atomic redeem: concurrent accepts — only one wins
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.invitation_token import InvitationToken
from app.models.profile import Profile
from app.models.user import User
from app.services.auth import hash_password, verify_password

# ── Helpers ───────────────────────────────────────────────────────────────────


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _get_profile_id(name: str = "admin") -> str:
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    async def _get():
        async with _TestSession() as session:
            result = await session.execute(select(Profile).where(Profile.name == name))
            p = result.scalar_one_or_none()
            return p.id if p else ""

    return _run(_get())


def _create_invited_user(email: str) -> tuple[str, str, str]:
    """Create an invited user + pending InvitationToken.

    Returns (user_id, raw_token, inv_token_id).
    """
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    profile_id = _get_profile_id("admin")
    user_id = str(uuid.uuid4())
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    inv_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    async def _do():
        async with _TestSession() as session:
            user = User(
                id=user_id,
                email=email,
                status="invited",
                profile_id=profile_id,
            )
            session.add(user)
            await session.flush()

            inv = InvitationToken(
                id=inv_id,
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
            session.add(inv)
            await session.commit()

    _run(_do())
    return user_id, raw_token, inv_id


def _create_expired_token(email: str) -> tuple[str, str]:
    """Create an invited user with an already-expired InvitationToken."""
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    profile_id = _get_profile_id("admin")
    user_id = str(uuid.uuid4())
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    async def _do():
        async with _TestSession() as session:
            user = User(
                id=user_id,
                email=email,
                status="invited",
                profile_id=profile_id,
            )
            session.add(user)
            await session.flush()

            inv = InvitationToken(
                id=str(uuid.uuid4()),
                user_id=user_id,
                token_hash=token_hash,
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
            session.add(inv)
            await session.commit()

    _run(_do())
    return user_id, raw_token


def _mark_token_used(inv_token_id: str) -> None:
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    async def _do():
        async with _TestSession() as session:
            inv = await session.get(InvitationToken, inv_token_id)
            if inv:
                inv.used_at = datetime.now(timezone.utc)
            await session.commit()

    _run(_do())


def _get_user(user_id: str) -> User | None:
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    async def _get():
        async with _TestSession() as session:
            return await session.get(User, user_id)

    return _run(_get())


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


# ── GET /api/invitations/{token} ─────────────────────────────────────────────


class TestGetInvitationInfo:
    def test_valid_token_returns_user_info(self, client):
        user_id, raw_token, _ = _create_invited_user("invite-info@example.com")
        resp = client.get(f"/api/invitations/{raw_token}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "invite-info@example.com"
        assert "profile_name" in data

    def test_invalid_token_returns_404(self, client):
        resp = client.get("/api/invitations/notarealtoken123")
        assert resp.status_code == 404

    def test_expired_token_returns_404(self, client):
        _, raw_token = _create_expired_token("invite-expired@example.com")
        resp = client.get(f"/api/invitations/{raw_token}")
        assert resp.status_code == 404

    def test_used_token_returns_410(self, client):
        _, raw_token, inv_id = _create_invited_user("invite-used@example.com")
        _mark_token_used(inv_id)
        resp = client.get(f"/api/invitations/{raw_token}")
        assert resp.status_code == 410


# ── POST /api/invitations/{token}/accept ────────────────────────────────────


VALID_PASSWORD = "ValidP4ss!word123"


class TestAcceptInvitation:
    def test_happy_path_returns_jwt_and_activates_user(self, client):
        user_id, raw_token, _ = _create_invited_user("accept-ok@example.com")
        resp = client.post(
            f"/api/invitations/{raw_token}/accept",
            json={"password": VALID_PASSWORD},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

        # User should now be active with a hashed password
        user = _get_user(user_id)
        assert user is not None
        assert user.status == "active"
        assert user.hashed_password is not None
        assert verify_password(VALID_PASSWORD, user.hashed_password)
        assert user.password_changed_at is not None
        assert user.last_login_at is not None

    def test_invalid_token_returns_410(self, client):
        resp = client.post(
            "/api/invitations/notarealtoken456/accept",
            json={"password": VALID_PASSWORD},
        )
        assert resp.status_code == 410

    def test_expired_token_returns_410(self, client):
        _, raw_token = _create_expired_token("accept-expired@example.com")
        resp = client.post(
            f"/api/invitations/{raw_token}/accept",
            json={"password": VALID_PASSWORD},
        )
        assert resp.status_code == 410

    def test_already_used_token_returns_410(self, client):
        _, raw_token, inv_id = _create_invited_user("accept-already-used@example.com")
        # First accept
        client.post(
            f"/api/invitations/{raw_token}/accept",
            json={"password": VALID_PASSWORD},
        )
        # Second accept — must be rejected
        resp = client.post(
            f"/api/invitations/{raw_token}/accept",
            json={"password": VALID_PASSWORD},
        )
        assert resp.status_code == 410

    def test_password_too_short_returns_422(self, client):
        _, raw_token, _ = _create_invited_user("accept-weakpw@example.com")
        resp = client.post(
            f"/api/invitations/{raw_token}/accept",
            json={"password": "short"},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "password_policy_violation"
        assert len(detail["failures"]) > 0

    def test_password_missing_uppercase_fails_policy(self, client):
        _, raw_token, _ = _create_invited_user("accept-noupper@example.com")
        resp = client.post(
            f"/api/invitations/{raw_token}/accept",
            json={"password": "nouppercase1!valid"},
        )
        assert resp.status_code == 422

    def test_token_not_reusable_after_accept(self, client):
        """Redeeming the same token twice: second attempt gets 410."""
        _, raw_token, _ = _create_invited_user("accept-reuse@example.com")
        r1 = client.post(
            f"/api/invitations/{raw_token}/accept",
            json={"password": VALID_PASSWORD},
        )
        assert r1.status_code == 200
        r2 = client.post(
            f"/api/invitations/{raw_token}/accept",
            json={"password": VALID_PASSWORD},
        )
        assert r2.status_code == 410


class TestAtomicRedeem:
    """Concurrent-accept race: only the first request should win."""

    def test_concurrent_accept_only_one_wins(self, client):
        """Send two concurrent accept requests; exactly one should get 200, the other 410."""
        _, raw_token, _ = _create_invited_user("accept-race@example.com")

        results = []

        def _do_accept():
            r = client.post(
                f"/api/invitations/{raw_token}/accept",
                json={"password": VALID_PASSWORD},
            )
            results.append(r.status_code)

        # Use threads to simulate near-simultaneous requests
        import threading

        t1 = threading.Thread(target=_do_accept)
        t2 = threading.Thread(target=_do_accept)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert sorted(results) == [200, 410]
