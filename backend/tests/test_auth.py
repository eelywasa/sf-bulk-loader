"""Tests for auth utilities (Ticket 2), auth API endpoints (Ticket 3),
and startup admin seed (Ticket 4)."""

import uuid

import pytest

from app.models.user import User
from app.services.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    seed_admin,
    validate_ws_token,
    verify_password,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_user(**kwargs) -> User:
    defaults = dict(
        id=str(uuid.uuid4()),
        username="alice",
        hashed_password=hash_password("secret"),
        role="user",
        is_active=True,
    )
    defaults.update(kwargs)
    return User(**defaults)


def _seed_user(client, username="alice", password="secret", role="user", is_active=True) -> dict:
    """Insert a user directly via the DB override and return basic info."""
    from tests.conftest import _TestSession, _run_async
    from sqlalchemy.ext.asyncio import AsyncSession

    user = _make_user(username=username, hashed_password=hash_password(password), role=role, is_active=is_active)

    async def _insert():
        async with _TestSession() as session:
            session.add(user)
            await session.commit()

    _run_async(_insert())
    return {"id": user.id, "username": user.username, "role": user.role}


# ── Password helpers ──────────────────────────────────────────────────────────


def test_hash_and_verify_password():
    hashed = hash_password("my-password")
    assert hashed != "my-password"
    assert verify_password("my-password", hashed)


def test_verify_password_wrong_password():
    hashed = hash_password("correct")
    assert not verify_password("wrong", hashed)


# ── JWT helpers ───────────────────────────────────────────────────────────────


def test_create_and_decode_token():
    user = _make_user()
    token = create_access_token(user)
    payload = decode_access_token(token)
    assert payload["sub"] == user.id
    assert payload["username"] == user.username
    assert payload["role"] == user.role
    assert "iat" in payload
    assert "exp" in payload


def test_decode_invalid_token_raises_401():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        decode_access_token("not.a.valid.token")
    assert exc_info.value.status_code == 401


def test_decode_tampered_token_raises_401():
    from fastapi import HTTPException

    user = _make_user()
    token = create_access_token(user)
    # Flip a character in the signature
    tampered = token[:-4] + "XXXX"
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(tampered)
    assert exc_info.value.status_code == 401


def test_decode_expired_token_raises_401():
    from datetime import datetime, timezone

    from fastapi import HTTPException
    from jose import jwt

    from app.config import settings

    payload = {
        "sub": str(uuid.uuid4()),
        "username": "expired",
        "role": "user",
        "iat": 1000,
        "exp": 1001,  # already in the past
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token)
    assert exc_info.value.status_code == 401


# ── WebSocket token helper ────────────────────────────────────────────────────


def test_validate_ws_token_valid():
    user = _make_user()
    token = create_access_token(user)
    payload = validate_ws_token(token)
    assert payload["sub"] == user.id


def test_validate_ws_token_missing_raises_401():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        validate_ws_token(None)
    assert exc_info.value.status_code == 401


def test_validate_ws_token_invalid_raises_401():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        validate_ws_token("garbage")
    assert exc_info.value.status_code == 401


# ── POST /api/auth/login ──────────────────────────────────────────────────────


def test_login_returns_token(client):
    _seed_user(client)
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body
    assert body["expires_in"] > 0


def test_login_wrong_password_returns_401(client):
    _seed_user(client)
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    assert resp.status_code == 401


def test_login_unknown_user_returns_401(client):
    resp = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    assert resp.status_code == 401


def test_login_inactive_user_returns_401(client):
    _seed_user(client, is_active=False)
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
    assert resp.status_code == 401


# ── GET /api/auth/me ──────────────────────────────────────────────────────────


def _auth_headers(client) -> dict:
    _seed_user(client)
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_me_returns_current_user(client):
    headers = _auth_headers(client)
    resp = client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "alice"
    assert body["role"] == "user"
    assert body["is_active"] is True


def test_me_no_token_returns_401(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_bad_token_returns_401(client):
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


# ── GET /api/auth/config ──────────────────────────────────────────────────────


def test_config_returns_saml_disabled(client):
    resp = client.get("/api/auth/config")
    assert resp.status_code == 200
    assert resp.json() == {"saml_enabled": False}


# ── POST /api/auth/logout ─────────────────────────────────────────────────────


def test_logout_returns_204(client):
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 204


# ── Startup admin seed (Ticket 4) ─────────────────────────────────────────────


def test_seed_admin_creates_admin_on_empty_db():
    """seed_admin creates an active admin user when the database is empty."""
    from sqlalchemy import select

    from app.config import settings
    from tests.conftest import _TestSession, _run_async

    async def _run():
        async with _TestSession() as session:
            await seed_admin(session)

        async with _TestSession() as session:
            result = await session.execute(select(User).where(User.username == settings.admin_username))
            user = result.scalar_one()
            assert user.role == "admin"
            assert user.is_active is True
            assert verify_password(settings.admin_password, user.hashed_password)

    _run_async(_run())


def test_seed_admin_is_idempotent():
    """seed_admin does not create a second user if one already exists."""
    from sqlalchemy import func, select

    from tests.conftest import _TestSession, _run_async

    async def _run():
        async with _TestSession() as session:
            await seed_admin(session)
        async with _TestSession() as session:
            await seed_admin(session)  # second call — should be a no-op

        async with _TestSession() as session:
            count = await session.scalar(select(func.count()).select_from(User))
            assert count == 1

    _run_async(_run())


def test_seed_admin_skips_when_users_exist():
    """seed_admin does nothing when at least one user already exists."""
    from sqlalchemy import func, select

    from tests.conftest import _TestSession, _run_async

    async def _run():
        # Pre-seed a user
        async with _TestSession() as session:
            session.add(_make_user(username="existing"))
            await session.commit()

        # seed_admin should not add another
        async with _TestSession() as session:
            await seed_admin(session)

        async with _TestSession() as session:
            count = await session.scalar(select(func.count()).select_from(User))
            assert count == 1

    _run_async(_run())


def test_seed_admin_fails_without_credentials():
    """seed_admin raises RuntimeError when DB is empty and env vars are absent."""
    from unittest.mock import patch

    from tests.conftest import _TestSession, _run_async

    async def _run():
        with patch("app.services.auth.settings") as mock_settings:
            mock_settings.admin_username = None
            mock_settings.admin_password = None
            async with _TestSession() as session:
                with pytest.raises(RuntimeError, match="ADMIN_USERNAME"):
                    await seed_admin(session)

    _run_async(_run())
