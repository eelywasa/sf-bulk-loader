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


def test_me_mfa_not_enrolled_when_no_totp_row(client):
    """User without a user_totp row → mfa.enrolled=false, backup_codes_remaining=0."""
    profile = _make_profile("viewer", RUNS_VIEW)
    user = _user_with_profile(profile, email="no-mfa@example.com")

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
    assert body["mfa"] == {
        "enrolled": False,
        "enrolled_at": None,
        "backup_codes_remaining": 0,
        "tenant_required": False,
    }


def test_me_mfa_enrolled_reports_backup_code_count(client):
    """User with a user_totp row and 7 unconsumed backup codes → mfa reflects that."""
    from datetime import datetime, timezone

    from app.models.user import User as UserModel
    from app.models.user_backup_code import UserBackupCode
    from app.models.user_totp import UserTotp
    from tests.conftest import _TestSession, _run_async

    user_id = str(uuid.uuid4())
    email = f"mfa-{uuid.uuid4().hex[:8]}@example.com"
    enrolled_at = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)

    async def _seed() -> None:
        async with _TestSession() as session:
            persisted = UserModel(
                id=user_id,
                email=email,
                status="active",
                is_admin=False,
            )
            session.add(persisted)
            await session.flush()
            session.add(
                UserTotp(
                    user_id=user_id,
                    secret_encrypted="ciphertext",
                    enrolled_at=enrolled_at,
                )
            )
            # 10 codes: 3 consumed, 7 still valid.
            for idx in range(10):
                session.add(
                    UserBackupCode(
                        user_id=user_id,
                        code_hash=f"hash-{idx:02d}",
                        consumed_at=(
                            datetime(2026, 4, 24, 13, 0, 0, tzinfo=timezone.utc)
                            if idx < 3
                            else None
                        ),
                    )
                )
            await session.commit()

    _run_async(_seed())

    # Build a detached in-memory User matching the persisted row so the /me
    # handler doesn't have to load relationships; the DB lookups for UserTotp
    # and UserBackupCode run against the seeded data via the real session.
    synthetic_user = UserModel(
        id=user_id, email=email, status="active", is_admin=False
    )
    synthetic_user.profile = None

    async def _override():
        return synthetic_user

    app.dependency_overrides[get_current_user] = _override
    try:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            resp = client.get("/api/auth/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mfa"]["enrolled"] is True
    assert body["mfa"]["enrolled_at"].startswith("2026-04-24T12:00:00")
    assert body["mfa"]["backup_codes_remaining"] == 7


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
    # Desktop mode reports not-enrolled; 2FA does not apply (spec §0 D2).
    assert body["mfa"] == {
        "enrolled": False,
        "enrolled_at": None,
        "backup_codes_remaining": 0,
        "tenant_required": False,
    }


def test_me_mfa_tenant_required_true_when_setting_on(client):
    """SFBL-251: mfa.tenant_required reflects the require_2fa setting."""
    profile = _make_profile("viewer", RUNS_VIEW)
    user = _user_with_profile(profile, email="tr-on@example.com")

    async def _override():
        return user

    class _FakeSvc:
        async def get(self, key):
            if key == "require_2fa":
                return True
            return None

    app.dependency_overrides[get_current_user] = _override
    try:
        with patch("app.config.settings") as mock_settings, patch(
            "app.services.settings.service.settings_service", new=_FakeSvc()
        ):
            mock_settings.auth_mode = "jwt"
            resp = client.get("/api/auth/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mfa"]["tenant_required"] is True


def test_me_mfa_tenant_required_false_when_setting_off(client):
    """SFBL-251: mfa.tenant_required is False when require_2fa is off."""
    profile = _make_profile("viewer", RUNS_VIEW)
    user = _user_with_profile(profile, email="tr-off@example.com")

    async def _override():
        return user

    class _FakeSvc:
        async def get(self, key):
            return False if key == "require_2fa" else None

    app.dependency_overrides[get_current_user] = _override
    try:
        with patch("app.config.settings") as mock_settings, patch(
            "app.services.settings.service.settings_service", new=_FakeSvc()
        ):
            mock_settings.auth_mode = "jwt"
            resp = client.get("/api/auth/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mfa"]["tenant_required"] is False
