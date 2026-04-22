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
    # Translate legacy is_active kwarg to the new status column.
    if "is_active" in kwargs:
        is_active = kwargs.pop("is_active")
        kwargs.setdefault("status", "active" if is_active else "deactivated")
    # Translate legacy role kwarg — role column dropped in migration 0022.
    role = kwargs.pop("role", None)
    if role == "admin" and "is_admin" not in kwargs:
        kwargs["is_admin"] = True
    defaults = dict(
        id=str(uuid.uuid4()),
        username="alice",
        hashed_password=hash_password("secret"),
        status="active",
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


# ── Ticket 12: Bootstrap password complexity ──────────────────────────────────


from app.services.auth import _validate_password_strength  # noqa: E402


@pytest.mark.parametrize("password,missing_rule", [
    ("Short1!",         "at least 12 characters"),
    ("alllowercase1!",  "at least one uppercase letter"),
    ("ALLUPPERCASE1!",  "at least one lowercase letter"),
    ("NoDigitsHere!x",  "at least one digit"),
    ("NoSpecialChar1A", "at least one special character"),
])
def test_validate_password_strength_rejects_weak_passwords(password, missing_rule):
    with pytest.raises(ValueError, match=missing_rule):
        _validate_password_strength(password)


def test_validate_password_strength_accepts_strong_password():
    _validate_password_strength("Str0ng&Secure#Pass")


def test_validate_password_strength_reports_all_failures():
    """A password that fails multiple rules lists every failure."""
    with pytest.raises(ValueError) as exc_info:
        _validate_password_strength("weak")
    msg = str(exc_info.value)
    assert "at least 12 characters" in msg
    assert "at least one uppercase letter" in msg
    assert "at least one digit" in msg
    assert "at least one special character" in msg


def test_seed_admin_fails_with_weak_password():
    """seed_admin raises RuntimeError when ADMIN_PASSWORD is too weak."""
    from unittest.mock import patch

    from tests.conftest import _TestSession, _run_async

    async def _run():
        with patch("app.services.auth.settings") as mock_settings:
            mock_settings.admin_username = "admin"
            mock_settings.admin_password = "weakpassword"
            async with _TestSession() as session:
                with pytest.raises(RuntimeError, match="minimum requirements"):
                    await seed_admin(session)

    _run_async(_run())


# ── Ticket 5: Protected route enforcement ─────────────────────────────────────


def test_protected_endpoint_without_token_returns_401(client):
    """Anonymous requests to protected REST endpoints must be rejected."""
    assert client.get("/api/connections/").status_code == 401
    assert client.get("/api/load-plans/").status_code == 401
    assert client.get("/api/runs/").status_code == 401
    assert client.get("/api/files/input").status_code == 401


def test_health_endpoint_is_public(client):
    """/api/health must remain accessible without authentication."""
    assert client.get("/api/health").status_code == 200


def test_auth_endpoints_are_public(client):
    """Auth endpoints must be reachable without a token."""
    assert client.get("/api/auth/config").status_code == 200
    assert client.post("/api/auth/logout").status_code == 204


# ── Ticket 6: initiated_by from authenticated user ────────────────────────────


def test_start_run_sets_initiated_by_from_token(client):
    """initiated_by on a new LoadRun must reflect the token's username."""
    from tests.conftest import _TestSession, _run_async
    from app.models.connection import Connection
    from app.models.load_plan import LoadPlan
    from app.services.salesforce_auth import encrypt_private_key

    # Seed a real user and log in.
    # is_admin=True so the RBAC migration-backstop allows the /run endpoint
    # (SFBL-195: is_admin users without a profile retain full access during transition).
    _seed_user(client, username="runner", password="pass123", role="admin")
    headers = {"Authorization": "Bearer " + client.post(
        "/api/auth/login", json={"username": "runner", "password": "pass123"}
    ).json()["access_token"]}

    # Seed a connection and plan directly via DB
    async def _seed_plan():
        async with _TestSession() as session:
            conn = Connection(
                name="C",
                instance_url="https://x.salesforce.com",
                login_url="https://login.salesforce.com",
                client_id="cid",
                private_key=encrypt_private_key("-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----"),
                username="u@x.com",
            )
            session.add(conn)
            await session.flush()
            plan = LoadPlan(name="P", connection_id=conn.id)
            session.add(plan)
            await session.commit()
            return plan.id

    plan_id = _run_async(_seed_plan())
    resp = client.post(f"/api/load-plans/{plan_id}/run", headers=headers)
    assert resp.status_code == 201
    assert resp.json()["initiated_by"] == "runner"


# ── Ticket 7: WebSocket token enforcement ─────────────────────────────────────


def _ws_token() -> str:
    """Generate a valid JWT for WebSocket authentication in tests."""
    user = _make_user(username="wsuser")
    return create_access_token(user)


def test_websocket_accepts_valid_token(client):
    """A connection carrying a valid JWT token is accepted."""
    token = _ws_token()
    with client.websocket_connect(f"/ws/runs/test-run?token={token}") as ws:
        data = ws.receive_json()
        assert data["event"] == "connected"


def test_websocket_rejects_missing_token(client):
    """A connection without a token receives close code 1008."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/runs/test-run"):
            pass
    assert exc_info.value.code == 1008


def test_websocket_rejects_invalid_token(client):
    """A connection with a malformed token receives close code 1008."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/runs/test-run?token=not.a.valid.jwt"):
            pass
    assert exc_info.value.code == 1008


# ── Ticket 2: Desktop profile auth bypass ─────────────────────────────────────


def test_seed_admin_skips_in_desktop_profile():
    """seed_admin is a no-op when auth_mode='none' — desktop needs no managed users."""
    from unittest.mock import patch, MagicMock

    from tests.conftest import _run_async

    async def _run():
        with patch("app.services.auth.settings") as mock_settings:
            mock_settings.auth_mode = "none"
            db = MagicMock()
            await seed_admin(db)
            # DB should never be touched
            db.scalar.assert_not_called()

    _run_async(_run())


def test_validate_ws_token_bypasses_in_desktop_profile():
    """validate_ws_token returns an empty dict without validation when auth_mode='none'."""
    from unittest.mock import patch

    with patch("app.services.auth.settings") as mock_settings:
        mock_settings.auth_mode = "none"
        result = validate_ws_token(None)  # no token supplied
        assert result == {}


def test_websocket_accepts_connection_without_token_in_desktop_profile(client):
    """In desktop profile (auth_mode=none) WS accepts connections with no token."""
    from unittest.mock import patch

    with patch("app.services.auth.settings") as mock_settings:
        mock_settings.auth_mode = "none"
        with client.websocket_connect("/ws/runs/test-run") as ws:
            data = ws.receive_json()
            assert data["event"] == "connected"
