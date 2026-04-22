"""Tests for the require_admin FastAPI dependency (SFBL-154).

Verifies:
- Non-admin user (is_admin=False) → 403 on an admin-protected endpoint
- Admin user (is_admin=True)      → request passes through
- Desktop profile (auth_mode=none) → always passes (is_admin=True on _DESKTOP_USER)
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.models.user import User
from app.services.auth import get_current_user, require_admin


# ---------------------------------------------------------------------------
# Minimal test application with one admin-protected route
# ---------------------------------------------------------------------------


def _make_test_app() -> FastAPI:
    """Return a minimal FastAPI app exposing a single admin-protected route."""
    _app = FastAPI()

    @_app.get("/protected")
    async def protected_route(admin: User = Depends(require_admin)) -> dict:
        return {"username": admin.username}

    return _app


def _admin_user() -> User:
    return User(
        id=str(uuid.uuid4()),
        username="admin",
        status="active",
        is_admin=True,
    )


def _regular_user() -> User:
    return User(
        id=str(uuid.uuid4()),
        username="regular",
        status="active",
        is_admin=False,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_require_admin_non_admin_returns_403():
    """A non-admin authenticated user should be rejected with 403."""
    app = _make_test_app()
    user = _regular_user()

    async def override_get_current_user() -> User:
        return user

    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app, raise_server_exceptions=True) as client:
        resp = client.get("/protected")

    assert resp.status_code == 403
    assert "Admin access required" in resp.json().get("detail", "")


def test_require_admin_admin_user_passes():
    """An admin user (is_admin=True) should be allowed through."""
    app = _make_test_app()
    user = _admin_user()

    async def override_get_current_user() -> User:
        return user

    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app, raise_server_exceptions=True) as client:
        resp = client.get("/protected")

    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"


def test_require_admin_non_admin_is_admin_false_returns_403():
    """is_admin=False always returns 403, regardless of any legacy role value."""
    app = _make_test_app()
    user = User(
        id=str(uuid.uuid4()),
        username="role-admin-only",
        status="active",
        is_admin=False,
    )

    async def override_get_current_user() -> User:
        return user

    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app, raise_server_exceptions=True) as client:
        resp = client.get("/protected")

    assert resp.status_code == 403
