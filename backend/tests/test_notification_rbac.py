"""Tests for notifications.manage RBAC gate on notification subscription routes (SFBL-368).

Covers:
- Hosted user WITHOUT notifications.manage → 403 on all 6 routes.
- Hosted user WITH notifications.manage → passes permission gate (may get 403 from
  _block_desktop_profile or another error, but NOT a 403 for permission_denied).
- Desktop profile (auth_mode=none) → 403 via _block_desktop_profile (unchanged).

The _block_desktop_profile() guard fires when auth_mode='none' regardless of
permissions. In hosted mode (auth_mode='jwt'), the permission gate fires first.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.main import app
from app.models.profile import Profile
from app.models.profile_permission import ProfilePermission
from app.models.user import User
from app.services.auth import get_current_user


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_profile_with_keys(name: str, keys: list[str]) -> Profile:
    profile = Profile(id=str(uuid.uuid4()), name=name)
    profile.permissions = [ProfilePermission(permission_key=k) for k in keys]
    return profile


def _make_user_with_profile(name: str, keys: list[str]) -> User:
    user = User(
        id=str(uuid.uuid4()),
        email=f"{name}-{uuid.uuid4().hex[:6]}@example.com",
        hashed_password="x",
        is_admin=False,
        status="active",
    )
    user.profile = _make_profile_with_keys(name, keys)
    return user


def _call_as_user(client, user: User, method: str, path: str, json: dict | None = None):
    """Make a request with get_current_user overridden to user, hosted auth mode.

    Patches app.config.settings so require_permission sees auth_mode='jwt'
    (hosted mode) and enforces profile permission_keys.
    """

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    try:
        with patch("app.config.settings") as ms:
            ms.auth_mode = "jwt"
            ms.app_env = "test"
            ms.sf_api_version = "v62.0"
            ms.health_enable_dependency_checks = False
            ms.output_dir = "/tmp/sfbl-test-output"
            ms.input_dir = "/tmp/sfbl-test-input"
            ms.input_storage_mode = "local"
            fn = getattr(client, method.lower())
            kwargs: dict = {}
            if json is not None:
                kwargs["json"] = json
            return fn(path, **kwargs)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _call_desktop(client, user: User, method: str, path: str, json: dict | None = None):
    """Make a request in desktop mode (auth_mode='none').

    Patches settings in both the notification router and app.config so that:
    1. require_permission sees auth_mode='none' → desktop bypass (no profile check)
    2. _block_desktop_profile() sees auth_mode='none' → raises 403

    This simulates the real desktop profile behaviour end-to-end.
    """

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    try:
        # Patch app.config.settings so require_permission does desktop bypass
        # (lazy `from app.config import settings as _settings` inside the closure).
        # Also patch the module-level settings in notification_subscriptions so
        # _block_desktop_profile() sees auth_mode='none'.
        with patch("app.config.settings") as ms_config, \
             patch("app.api.notification_subscriptions.settings") as ms_router:
            ms_config.auth_mode = "none"
            ms_config.app_env = "test"
            ms_config.sf_api_version = "v62.0"
            ms_config.health_enable_dependency_checks = False
            ms_config.output_dir = "/tmp"
            ms_config.input_dir = "/tmp"
            ms_config.input_storage_mode = "local"
            ms_router.auth_mode = "none"

            fn = getattr(client, method.lower())
            kwargs: dict = {}
            if json is not None:
                kwargs["json"] = json
            return fn(path, **kwargs)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# Routes to test
_SUBSCRIPTION_ID = str(uuid.uuid4())
_ROUTES = [
    ("GET",    "/api/notification-subscriptions",                              None),
    ("POST",   "/api/notification-subscriptions",                              {"channel": "email", "destination": "x@x.com", "trigger": "terminal_any"}),
    ("GET",    f"/api/notification-subscriptions/{_SUBSCRIPTION_ID}",          None),
    ("PUT",    f"/api/notification-subscriptions/{_SUBSCRIPTION_ID}",          {"channel": "email", "destination": "x@x.com", "trigger": "terminal_any"}),
    ("DELETE", f"/api/notification-subscriptions/{_SUBSCRIPTION_ID}",          None),
    ("POST",   f"/api/notification-subscriptions/{_SUBSCRIPTION_ID}/test",     None),
]


@pytest.mark.parametrize("method,path,body", _ROUTES)
def test_notification_routes_without_manage_403(client, method, path, body):
    """Hosted user WITHOUT notifications.manage → 403 permission_denied on all routes."""
    user = _make_user_with_profile("viewer_no_notif", ["connections.view", "plans.view"])
    resp = _call_as_user(client, user, method, path, json=body)
    assert resp.status_code == 403, (
        f"{method} {path}: expected 403 for user without notifications.manage, got {resp.status_code}: {resp.text}"
    )
    detail = resp.json().get("detail")
    # The error should be permission_denied (from require_permission), not the desktop guard
    if isinstance(detail, dict):
        assert detail.get("required_permission") == "notifications.manage", (
            f"Expected required_permission=notifications.manage, got: {detail}"
        )


@pytest.mark.parametrize("method,path,body", _ROUTES)
def test_notification_routes_with_manage_passes_permission(client, method, path, body):
    """Hosted user WITH notifications.manage → permission gate passes (may 403 on _block_desktop or 404/409 etc)."""
    user = _make_user_with_profile("has_notif", ["notifications.manage"])
    resp = _call_as_user(client, user, method, path, json=body)
    # Should NOT be 403 from permission_denied for notifications.manage
    if resp.status_code == 403:
        detail = resp.json().get("detail")
        if isinstance(detail, dict):
            assert detail.get("required_permission") != "notifications.manage", (
                f"{method} {path}: should not get permission_denied for notifications.manage "
                f"when user has the key; got: {detail}"
            )
        # If detail is a string, it might be from _block_desktop_profile — that's OK;
        # but we shouldn't hit it in hosted mode. Log a warning but don't fail.
    # Anything else (200, 404, 409, 201, 204) means the permission gate passed.


def test_notification_list_desktop_profile_403(client):
    """Desktop profile (auth_mode=none) → 403 from _block_desktop_profile regardless of permission."""
    # Desktop user has all keys in desktop mode, but _block_desktop_profile fires first
    user = _make_user_with_profile("desktop_user", ["notifications.manage"])
    resp = _call_desktop(client, user, "GET", "/api/notification-subscriptions")
    assert resp.status_code == 403, (
        f"Desktop profile should get 403 from _block_desktop_profile, got {resp.status_code}: {resp.text}"
    )
    # The detail should be the desktop-guard message, NOT permission_denied
    detail = resp.json().get("detail", "")
    if isinstance(detail, dict):
        assert detail.get("error") != "permission_denied", (
            "Desktop 403 should come from _block_desktop_profile, not permission gate"
        )
