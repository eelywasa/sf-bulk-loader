"""Tests for /api/admin/users/* endpoints (SFBL-200).

Coverage
--------
- GET /api/admin/users           — list (status filter, include_deleted, pagination)
- POST /api/admin/users          — invite (happy path, email collision, bad profile)
- GET /api/admin/users/{id}      — detail (happy path, 404)
- PUT /api/admin/users/{id}      — update profile/display_name; last-admin guard
- POST /api/admin/users/{id}/unlock      — clear lockout
- POST /api/admin/users/{id}/deactivate — active→deactivated; status guard; last-admin guard
- POST /api/admin/users/{id}/reactivate — deactivated→active; status guard
- POST /api/admin/users/{id}/reset-password — temp password issuance
- POST /api/admin/users/{id}/resend-invite  — new token for invited user
- DELETE /api/admin/users/{id}   — soft delete; bootstrap guard; last-admin guard; self-delete guard

Permission checks
-----------------
- Non-admin (no users.manage permission) → 403 on every endpoint.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import settings
from app.models.profile import Profile
from app.models.user import User
from app.services.auth import get_current_user, hash_password

# ── DB helpers ─────────────────────────────────────────────────────────────────


def _run(coro):
    """Run a coroutine in a fresh event loop (safe outside any running loop)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _get_profile_id(name: str) -> str:
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    async def _get():
        async with _TestSession() as session:
            result = await session.execute(select(Profile).where(Profile.name == name))
            p = result.scalar_one_or_none()
            return p.id if p else ""

    return _run(_get())


def _insert_user(
    email: str,
    status: str = "active",
    profile_id: str | None = None,
    is_admin: bool = False,
) -> str:
    """Insert a user into the test DB and return its id."""
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    new_id = str(uuid.uuid4())

    async def _do():
        async with _TestSession() as session:
            user = User(
                id=new_id,
                email=email,
                hashed_password=hash_password("Passw0rd!"),
                status=status,
                is_admin=is_admin,
                profile_id=profile_id,
            )
            session.add(user)
            await session.commit()

    _run(_do())
    return new_id


def _make_no_permission_user() -> User:
    """A user with no profile — will fail require_permission checks."""
    return User(
        id=str(uuid.uuid4()),
        email=f"noperm-{uuid.uuid4().hex[:6]}@example.com",
        hashed_password=hash_password("NoP3rm!"),
        is_admin=False,
        status="active",
        profile_id=None,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Permission denial tests (non-admin user on every endpoint)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET",    "/api/admin/users"),
        ("POST",   "/api/admin/users"),
        ("GET",    "/api/admin/users/some-id"),
        ("PUT",    "/api/admin/users/some-id"),
        ("POST",   "/api/admin/users/some-id/unlock"),
        ("POST",   "/api/admin/users/some-id/deactivate"),
        ("POST",   "/api/admin/users/some-id/reactivate"),
        ("POST",   "/api/admin/users/some-id/reset-password"),
        ("POST",   "/api/admin/users/some-id/resend-invite"),
        ("DELETE", "/api/admin/users/some-id"),
    ],
)
def test_non_admin_gets_403(method: str, path: str):
    """A user without users.manage permission must receive 403 on every endpoint.

    Override get_current_user to return a user with no profile (no permissions).
    The require_permission dependency sees no profile → 403.
    """
    from app.database import get_db
    from app.main import app as _app
    from app.services.auth import get_current_user as _gcu
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    viewer = _make_no_permission_user()

    async def _override_viewer():
        return viewer

    async def _override_db():
        async with _TestSession() as session:
            yield session

    _app.dependency_overrides[_gcu] = _override_viewer
    _app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(_app, raise_server_exceptions=False) as c:
            fn = getattr(c, method.lower())
            if method in ("POST", "PUT"):
                r = fn(path, json={})
            else:
                r = fn(path)
        assert r.status_code == 403, (
            f"{method} {path} → expected 403, got {r.status_code}: {r.text}"
        )
    finally:
        _app.dependency_overrides.pop(_gcu, None)
        _app.dependency_overrides.pop(get_db, None)


# ──────────────────────────────────────────────────────────────────────────────
# List users
# ──────────────────────────────────────────────────────────────────────────────


def test_list_users_returns_paginated_results(auth_client: TestClient):
    _insert_user(f"list-target-{uuid.uuid4().hex[:6]}@ex.com")
    resp = auth_client.get("/api/admin/users")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert body["page"] == 1


def test_list_users_excludes_deleted_by_default(auth_client: TestClient):
    email = f"deleted-{uuid.uuid4().hex[:6]}@ex.com"
    _insert_user(email, status="deleted")

    resp = auth_client.get("/api/admin/users")
    assert resp.status_code == 200
    emails = [u["email"] for u in resp.json()["items"]]
    assert email not in emails


def test_list_users_includes_deleted_with_flag(auth_client: TestClient):
    email = f"deleted-flag-{uuid.uuid4().hex[:6]}@ex.com"
    _insert_user(email, status="deleted")

    resp = auth_client.get("/api/admin/users?include_deleted=true")
    assert resp.status_code == 200
    emails = [u["email"] for u in resp.json()["items"]]
    assert email in emails


def test_list_users_status_filter(auth_client: TestClient):
    email_active = f"active-{uuid.uuid4().hex[:6]}@ex.com"
    email_deact = f"deact-{uuid.uuid4().hex[:6]}@ex.com"
    _insert_user(email_active, status="active")
    _insert_user(email_deact, status="deactivated")

    resp = auth_client.get("/api/admin/users?status=deactivated")
    assert resp.status_code == 200
    emails = [u["email"] for u in resp.json()["items"]]
    assert email_deact in emails
    assert email_active not in emails


# ──────────────────────────────────────────────────────────────────────────────
# Get user detail
# ──────────────────────────────────────────────────────────────────────────────


def test_get_user_returns_detail(auth_client: TestClient):
    email = f"detail-{uuid.uuid4().hex[:6]}@ex.com"
    uid = _insert_user(email)
    resp = auth_client.get(f"/api/admin/users/{uid}")
    assert resp.status_code == 200
    assert resp.json()["email"] == email


def test_get_user_404_for_unknown_id(auth_client: TestClient):
    resp = auth_client.get("/api/admin/users/nonexistent-id-abc")
    assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# Invite user
# ──────────────────────────────────────────────────────────────────────────────


def test_invite_user_creates_pending_user_and_returns_token(auth_client: TestClient):
    admin_pid = _get_profile_id("admin")
    email = f"invite-{uuid.uuid4().hex[:6]}@ex.com"

    resp = auth_client.post(
        "/api/admin/users",
        json={"email": email, "profile_id": admin_pid},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user"]["status"] == "invited"
    assert body["user"]["email"] == email
    assert "raw_token" in body
    assert len(body["raw_token"]) > 10
    assert "expires_at" in body


def test_invite_user_email_collision_returns_409(auth_client: TestClient):
    admin_pid = _get_profile_id("admin")
    email = f"collision-{uuid.uuid4().hex[:6]}@ex.com"
    _insert_user(email)

    resp = auth_client.post(
        "/api/admin/users",
        json={"email": email, "profile_id": admin_pid},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "email_in_use"


def test_invite_user_bad_profile_id_returns_422(auth_client: TestClient):
    resp = auth_client.post(
        "/api/admin/users",
        json={"email": f"bad-profile-{uuid.uuid4().hex[:6]}@ex.com", "profile_id": "nonexistent"},
    )
    assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────────────────
# Update user
# ──────────────────────────────────────────────────────────────────────────────


def test_update_user_display_name(auth_client: TestClient):
    uid = _insert_user(f"update-name-{uuid.uuid4().hex[:6]}@ex.com")
    resp = auth_client.put(f"/api/admin/users/{uid}", json={"display_name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "New Name"


def test_update_user_profile_id(auth_client: TestClient):
    op_pid = _get_profile_id("operator")
    uid = _insert_user(f"update-prof-{uuid.uuid4().hex[:6]}@ex.com")
    resp = auth_client.put(f"/api/admin/users/{uid}", json={"profile_id": op_pid})
    assert resp.status_code == 200
    assert resp.json()["profile"]["name"] == "operator"


def test_update_user_last_admin_guard(auth_client: TestClient):
    """Demoting the last admin profile user returns 409."""
    admin_pid = _get_profile_id("admin")
    op_pid = _get_profile_id("operator")
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    # Clear all admin-profile users first so we can be certain of the count
    async def _clear_admin_profile():
        async with _TestSession() as session:
            result = await session.execute(
                select(User).where(User.profile_id == admin_pid)
            )
            for u in result.scalars().all():
                u.profile_id = op_pid
            await session.commit()

    _run(_clear_admin_profile())

    # Insert exactly ONE admin-profile user
    uid = _insert_user(
        f"solo-admin-{uuid.uuid4().hex[:6]}@ex.com",
        status="active",
        profile_id=admin_pid,
        is_admin=True,
    )

    # Demoting the sole admin should be blocked
    resp = auth_client.put(f"/api/admin/users/{uid}", json={"profile_id": op_pid})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "last_admin_guard"


# ──────────────────────────────────────────────────────────────────────────────
# Deactivate / reactivate
# ──────────────────────────────────────────────────────────────────────────────


def test_deactivate_active_user(auth_client: TestClient):
    uid = _insert_user(f"deact-{uuid.uuid4().hex[:6]}@ex.com", status="active")
    resp = auth_client.post(f"/api/admin/users/{uid}/deactivate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deactivated"


def test_deactivate_already_deactivated_returns_409(auth_client: TestClient):
    uid = _insert_user(f"already-deact-{uuid.uuid4().hex[:6]}@ex.com", status="deactivated")
    resp = auth_client.post(f"/api/admin/users/{uid}/deactivate")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "invalid_status_transition"


def test_deactivate_last_admin_guard(auth_client: TestClient):
    """Deactivating the last active admin profile user returns 409."""
    admin_pid = _get_profile_id("admin")
    op_pid = _get_profile_id("operator")
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    # Clear all admin-profile users
    async def _clear_admin_profile():
        async with _TestSession() as session:
            result = await session.execute(
                select(User).where(User.profile_id == admin_pid)
            )
            for u in result.scalars().all():
                u.profile_id = op_pid
            await session.commit()

    _run(_clear_admin_profile())

    # Insert exactly ONE active admin-profile user
    uid = _insert_user(
        f"last-admin-deact-{uuid.uuid4().hex[:6]}@ex.com",
        status="active",
        profile_id=admin_pid,
        is_admin=True,
    )

    resp = auth_client.post(f"/api/admin/users/{uid}/deactivate")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "last_admin_guard"


def test_reactivate_deactivated_user(auth_client: TestClient):
    uid = _insert_user(f"react-{uuid.uuid4().hex[:6]}@ex.com", status="deactivated")
    resp = auth_client.post(f"/api/admin/users/{uid}/reactivate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


def test_reactivate_active_user_returns_409(auth_client: TestClient):
    uid = _insert_user(f"already-active-{uuid.uuid4().hex[:6]}@ex.com", status="active")
    resp = auth_client.post(f"/api/admin/users/{uid}/reactivate")
    assert resp.status_code == 409
    assert resp.json()["detail"]["current_status"] == "active"


# ──────────────────────────────────────────────────────────────────────────────
# Reset password
# ──────────────────────────────────────────────────────────────────────────────


def test_reset_password_returns_temp_password(auth_client: TestClient):
    uid = _insert_user(f"reset-pwd-{uuid.uuid4().hex[:6]}@ex.com")
    resp = auth_client.post(f"/api/admin/users/{uid}/reset-password")
    assert resp.status_code == 200
    body = resp.json()
    assert "temp_password" in body
    assert len(body["temp_password"]) >= 12
    assert body["must_reset_password"] is True


def test_reset_password_deleted_user_returns_409(auth_client: TestClient):
    uid = _insert_user(f"reset-deleted-{uuid.uuid4().hex[:6]}@ex.com", status="deleted")
    resp = auth_client.post(f"/api/admin/users/{uid}/reset-password")
    assert resp.status_code == 409


# ──────────────────────────────────────────────────────────────────────────────
# Resend invite
# ──────────────────────────────────────────────────────────────────────────────


def test_resend_invite_returns_new_token(auth_client: TestClient):
    uid = _insert_user(f"resend-{uuid.uuid4().hex[:6]}@ex.com", status="invited")
    resp = auth_client.post(f"/api/admin/users/{uid}/resend-invite")
    assert resp.status_code == 200
    body = resp.json()
    assert "raw_token" in body
    assert "expires_at" in body


def test_resend_invite_non_invited_user_returns_409(auth_client: TestClient):
    uid = _insert_user(f"resend-active-{uuid.uuid4().hex[:6]}@ex.com", status="active")
    resp = auth_client.post(f"/api/admin/users/{uid}/resend-invite")
    assert resp.status_code == 409
    assert resp.json()["detail"]["current_status"] == "active"


# ──────────────────────────────────────────────────────────────────────────────
# Soft delete
# ──────────────────────────────────────────────────────────────────────────────


def test_soft_delete_user(auth_client: TestClient):
    uid = _insert_user(f"softdel-{uuid.uuid4().hex[:6]}@ex.com")
    resp = auth_client.delete(f"/api/admin/users/{uid}")
    assert resp.status_code == 204

    # Confirm user is now deleted
    resp2 = auth_client.get(f"/api/admin/users/{uid}")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "deleted"


def test_soft_delete_bootstrap_admin_returns_409(auth_client: TestClient):
    """Bootstrap admin (settings.admin_email) cannot be deleted."""
    boot_email = settings.admin_email or "test-admin@example.com"
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    async def _get_boot_id():
        async with _TestSession() as session:
            result = await session.execute(select(User).where(User.email == boot_email))
            u = result.scalar_one_or_none()
            return u.id if u else None

    boot_id = _run(_get_boot_id())
    if boot_id is None:
        pytest.skip("Bootstrap admin not found in test DB")

    resp = auth_client.delete(f"/api/admin/users/{boot_id}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "bootstrap_admin_protected"


def test_soft_delete_nonexistent_user_returns_404(auth_client: TestClient):
    resp = auth_client.delete("/api/admin/users/nonexistent-xyz")
    assert resp.status_code == 404


def test_soft_delete_idempotent_for_already_deleted(auth_client: TestClient):
    """Deleting an already-deleted user returns 204 (idempotent)."""
    uid = _insert_user(f"idem-del-{uuid.uuid4().hex[:6]}@ex.com", status="deleted")
    resp = auth_client.delete(f"/api/admin/users/{uid}")
    assert resp.status_code == 204


def test_soft_deleted_user_excluded_from_default_list(auth_client: TestClient):
    """After soft-delete, user no longer appears in the default list."""
    email = f"del-list-{uuid.uuid4().hex[:6]}@ex.com"
    uid = _insert_user(email)

    auth_client.delete(f"/api/admin/users/{uid}")

    resp = auth_client.get("/api/admin/users")
    emails = [u["email"] for u in resp.json()["items"]]
    assert email not in emails


def test_soft_delete_last_admin_guard(auth_client: TestClient):
    """Deleting the last active admin profile user returns 409."""
    admin_pid = _get_profile_id("admin")
    op_pid = _get_profile_id("operator")
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    # Clear all admin-profile users
    async def _clear_admin_profile():
        async with _TestSession() as session:
            result = await session.execute(
                select(User).where(User.profile_id == admin_pid)
            )
            for u in result.scalars().all():
                u.profile_id = op_pid
            await session.commit()

    _run(_clear_admin_profile())

    # Insert exactly ONE admin-profile user
    uid = _insert_user(
        f"last-admin-del-{uuid.uuid4().hex[:6]}@ex.com",
        status="active",
        profile_id=admin_pid,
        is_admin=True,
    )

    resp = auth_client.delete(f"/api/admin/users/{uid}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "last_admin_guard"


# ──────────────────────────────────────────────────────────────────────────────
# Unlock (SFBL-191 — kept endpoint)
# ──────────────────────────────────────────────────────────────────────────────


def test_unlock_locked_user(auth_client: TestClient):
    uid = _insert_user(f"unlock-{uuid.uuid4().hex[:6]}@ex.com", status="locked")
    resp = auth_client.post(f"/api/admin/users/{uid}/unlock")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"
