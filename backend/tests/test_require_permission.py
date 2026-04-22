"""Unit tests for require_permission() factory (SFBL-195).

Covers:
- Factory raises ValueError immediately for unknown key (fail-fast)
- Factory returns a callable for a valid key
- 200 when user has the required permission
- 403 when user lacks the required permission (structured detail body)
- 403 detail contains the expected fields
- Desktop mode (auth_mode=none) always passes through (no profile on virtual user)
- WARN log emitted on permission denial with correct event_name / fields
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth.permissions import (
    ALL_PERMISSION_KEYS,
    PLANS_VIEW,
    RUNS_VIEW,
    require_permission,
)
from app.models.profile import Profile
from app.models.profile_permission import ProfilePermission
from app.models.user import User
from app.services.auth import get_current_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(name: str, *keys: str) -> Profile:
    """Build an in-memory Profile with the given permission keys."""
    profile = Profile(id=str(uuid.uuid4()), name=name)
    profile.permissions = [ProfilePermission(permission_key=k) for k in keys]
    return profile


def _user_with_profile(profile: Profile) -> User:
    user = User(id=str(uuid.uuid4()), email="test@example.com", status="active", is_admin=False)
    user.profile = profile
    return user


def _user_without_profile() -> User:
    user = User(id=str(uuid.uuid4()), email="desktop@localhost", status="active", is_admin=True)
    user.profile = None
    return user


def _make_app(key: str) -> FastAPI:
    _app = FastAPI()
    dep = require_permission(key)

    @_app.get("/protected")
    async def protected(user: User = Depends(dep)) -> dict:
        return {"email": user.email}

    return _app


# ---------------------------------------------------------------------------
# Factory-call validation
# ---------------------------------------------------------------------------


def test_require_permission_unknown_key_raises_at_factory_call():
    """Passing an unknown key must raise ValueError at factory-call time."""
    with pytest.raises(ValueError, match="unknown permission key"):
        require_permission("bogus.key")


def test_require_permission_known_key_returns_callable():
    dep = require_permission(PLANS_VIEW)
    assert callable(dep)


# ---------------------------------------------------------------------------
# Hosted mode — user HAS the permission
# ---------------------------------------------------------------------------


def test_require_permission_user_has_permission_returns_200():
    profile = _make_profile("admin", PLANS_VIEW)
    user = _user_with_profile(profile)
    app = _make_app(PLANS_VIEW)

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    with TestClient(app, raise_server_exceptions=True) as client:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            resp = client.get("/protected")

    assert resp.status_code == 200
    assert resp.json()["email"] == "test@example.com"


# ---------------------------------------------------------------------------
# Hosted mode — user LACKS the permission
# ---------------------------------------------------------------------------


def test_require_permission_user_lacks_permission_returns_403():
    profile = _make_profile("viewer")  # no keys
    user = _user_with_profile(profile)
    app = _make_app(PLANS_VIEW)

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    with TestClient(app, raise_server_exceptions=True) as client:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            resp = client.get("/protected")

    assert resp.status_code == 403
    body = resp.json()
    assert body["detail"]["error"] == "permission_denied"
    assert body["detail"]["required_permission"] == PLANS_VIEW


def test_require_permission_no_profile_returns_403():
    """A user with no profile at all (profile=None) in hosted mode gets 403."""
    user = User(id=str(uuid.uuid4()), email="profileless@example.com", status="active", is_admin=False)
    user.profile = None
    app = _make_app(RUNS_VIEW)

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    with TestClient(app, raise_server_exceptions=True) as client:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            resp = client.get("/protected")

    assert resp.status_code == 403
    assert resp.json()["detail"]["required_permission"] == RUNS_VIEW


def test_require_permission_admin_flag_without_profile_gets_403():
    """is_admin=True without a profile assigned must NOT bypass the permission check.

    The migration backstop (SFBL-Epic-B) has been removed — all users now have
    profile_id NOT NULL (migration 0022).  This test confirms the legacy backstop
    path no longer exists: a user who somehow has is_admin=True but profile=None
    is denied in hosted mode just like any other profileless user.
    """
    user = User(id=str(uuid.uuid4()), email="oldadmin@example.com", status="active", is_admin=True)
    user.profile = None
    app = _make_app(PLANS_VIEW)

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    with TestClient(app, raise_server_exceptions=True) as client:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            resp = client.get("/protected")

    assert resp.status_code == 403
    assert resp.json()["detail"]["required_permission"] == PLANS_VIEW


# ---------------------------------------------------------------------------
# Desktop mode — always passes (no profile required)
# ---------------------------------------------------------------------------


def test_require_permission_desktop_mode_always_passes():
    """In desktop mode (auth_mode=none) the gate is a no-op."""
    user = _user_without_profile()
    app = _make_app(PLANS_VIEW)

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    with TestClient(app, raise_server_exceptions=True) as client:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "none"
            resp = client.get("/protected")

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Observability — WARN log emitted on denial
# ---------------------------------------------------------------------------


def test_require_permission_warn_log_on_denial(caplog):
    """Permission denial must emit a WARN log with the expected event_name."""
    profile = _make_profile("viewer")  # no keys
    user = _user_with_profile(profile)
    app = _make_app(PLANS_VIEW)

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override

    with caplog.at_level(logging.WARNING, logger="app.auth.permissions"):
        with TestClient(app, raise_server_exceptions=True) as client:
            with patch("app.config.settings") as mock_settings:
                mock_settings.auth_mode = "jwt"
                client.get("/protected")

    assert any(
        r.getMessage() == "Permission denied"
        and r.levelno == logging.WARNING
        for r in caplog.records
    ), f"Expected WARNING 'Permission denied' in logs; got: {[r.getMessage() for r in caplog.records]}"
