"""Tests for GET /api/auth/me — ensures profile + permissions fields (SFBL-195).

Contract with SFBL-196 (frontend):
  - response.profile.name: "admin" | "operator" | "viewer" | "desktop"
  - response.permissions: sorted list of permission key strings

Coverage:
  - Admin user (with admin profile) → profile.name="admin", permissions=[all admin keys]
  - Operator user (with operator profile) → profile.name="operator", subset permissions
  - Viewer user (with viewer profile) → profile.name="viewer", smaller subset
  - No profile (legacy or invalid state) → profile=null, permissions=[]
  - Desktop mode (auth_mode=none) → profile.name="desktop", permissions=all keys sorted
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.auth.permissions import ALL_PERMISSION_KEYS, PLANS_VIEW, RUNS_VIEW, SYSTEM_SETTINGS
from app.main import app
from app.models.profile import Profile
from app.models.profile_permission import ProfilePermission
from app.models.user import User
from app.services.auth import get_current_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(name: str, *keys: str) -> Profile:
    profile = Profile(id=str(uuid.uuid4()), name=name)
    profile.permissions = [ProfilePermission(permission_key=k) for k in keys]
    return profile


def _user_with_profile(profile: Profile | None, email: str = "test@example.com") -> User:
    user = User(id=str(uuid.uuid4()), email=email, status="active", is_admin=False)
    user.profile = profile
    return user


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_me_admin_profile(client):
    """Admin user with system.settings + all keys → correct profile name + permissions."""
    admin_keys = sorted(ALL_PERMISSION_KEYS)
    profile = _make_profile("admin", *admin_keys)
    user = _user_with_profile(profile, email="admin-user@example.com")
    user.is_admin = True

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    try:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            resp = client.get("/api/auth/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile"]["name"] == "admin"
    assert sorted(body["permissions"]) == admin_keys


def test_me_operator_profile(client):
    """Operator user with a subset of permissions → correct profile + permissions."""
    operator_keys = [PLANS_VIEW, RUNS_VIEW]
    profile = _make_profile("operator", *operator_keys)
    user = _user_with_profile(profile)

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    try:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            resp = client.get("/api/auth/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile"]["name"] == "operator"
    assert sorted(body["permissions"]) == sorted(operator_keys)


def test_me_viewer_profile(client):
    """Viewer with read-only permission → profile.name=viewer, single key."""
    profile = _make_profile("viewer", RUNS_VIEW)
    user = _user_with_profile(profile)

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    try:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            resp = client.get("/api/auth/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile"]["name"] == "viewer"
    assert body["permissions"] == [RUNS_VIEW]


def test_me_no_profile(client):
    """User with no profile → profile=null, permissions=[]."""
    user = _user_with_profile(None)

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    try:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            resp = client.get("/api/auth/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile"] is None
    assert body["permissions"] == []


def test_me_desktop_mode(client):
    """Desktop mode (auth_mode=none) → profile.name=desktop, all keys sorted."""
    user = User(id="desktop", email="desktop@localhost", status="active", is_admin=True)
    user.profile = None  # desktop user has no DB profile

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    try:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "none"
            resp = client.get("/api/auth/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile"]["name"] == "desktop"
    assert body["permissions"] == sorted(ALL_PERMISSION_KEYS)
