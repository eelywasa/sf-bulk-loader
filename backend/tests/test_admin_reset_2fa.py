"""Tests for POST /api/admin/users/{user_id}/reset-2fa + CLI `admin-recover`
2FA reset (SFBL-249).

Covers:
- Happy path: clears UserTotp + UserBackupCode rows, bumps password_changed_at,
  writes login_attempt audit row, idempotent when no factor present.
- Permission gate: `admin.users.reset_2fa` required — a user with
  `users.manage` but NOT `admin.users.reset_2fa` is refused 403.
- CLI break-glass: `admin-recover` clears 2FA state by default; `--keep-2fa`
  preserves it.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.permissions import USERS_MANAGE
from app.models.login_attempt import LoginAttempt
from app.models.profile import Profile
from app.models.profile_permission import ProfilePermission
from app.models.user import User
from app.models.user_backup_code import UserBackupCode
from app.models.user_totp import UserTotp
from app.services.auth import hash_password


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _insert_user(email: str, is_admin: bool = False) -> str:
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    uid = str(uuid.uuid4())

    async def _do():
        async with _TestSession() as session:
            session.add(
                User(
                    id=uid,
                    email=email,
                    hashed_password=hash_password("Passw0rd!"),
                    status="active",
                    is_admin=is_admin,
                )
            )
            await session.commit()

    _run(_do())
    return uid


def _seed_totp_and_codes(user_id: str, num_codes: int = 10) -> None:
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    async def _do():
        async with _TestSession() as session:
            session.add(
                UserTotp(
                    user_id=user_id,
                    secret_encrypted="enc:fake",
                )
            )
            for _ in range(num_codes):
                session.add(
                    UserBackupCode(
                        user_id=user_id,
                        # 60-char bcrypt-shaped placeholder, real hashes not
                        # required for this test.
                        code_hash="$2b$04$" + ("x" * 53),
                    )
                )
            await session.commit()

    _run(_do())


def _count_totp(user_id: str) -> int:
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    async def _do():
        async with _TestSession() as session:
            rows = await session.execute(
                select(UserTotp).where(UserTotp.user_id == user_id)
            )
            return len(rows.scalars().all())

    return _run(_do())


def _count_backup_codes(user_id: str) -> int:
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    async def _do():
        async with _TestSession() as session:
            rows = await session.execute(
                select(UserBackupCode).where(UserBackupCode.user_id == user_id)
            )
            return len(rows.scalars().all())

    return _run(_do())


def _get_user(user_id: str) -> User:
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    async def _do():
        async with _TestSession() as session:
            return (
                await session.execute(select(User).where(User.id == user_id))
            ).scalar_one()

    return _run(_do())


def _count_login_attempts(user_id: str, outcome: str) -> int:
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    async def _do():
        async with _TestSession() as session:
            rows = await session.execute(
                select(LoginAttempt).where(
                    (LoginAttempt.user_id == user_id)
                    & (LoginAttempt.outcome == outcome)
                )
            )
            return len(rows.scalars().all())

    return _run(_do())


# ── Endpoint tests ────────────────────────────────────────────────────────────


def test_reset_2fa_happy_path(auth_client: TestClient):
    """Admin clears target user's TOTP + backup codes; audit + watermark updated."""
    target_id = _insert_user(f"reset-{uuid.uuid4().hex[:6]}@ex.com")
    _seed_totp_and_codes(target_id, num_codes=10)
    assert _count_totp(target_id) == 1
    assert _count_backup_codes(target_id) == 10

    before = _get_user(target_id)
    before_pca = before.password_changed_at

    resp = auth_client.post(f"/api/admin/users/{target_id}/reset-2fa")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == target_id
    assert body["had_factor"] is True
    assert body["backup_codes_cleared"] == 10

    assert _count_totp(target_id) == 0
    assert _count_backup_codes(target_id) == 0

    after = _get_user(target_id)
    assert after.password_changed_at is not None
    assert before_pca is None or after.password_changed_at > before_pca

    assert _count_login_attempts(target_id, "admin_reset_2fa") == 1


def test_reset_2fa_idempotent_no_factor(auth_client: TestClient):
    """Resetting a user without an enrolled factor still succeeds (idempotent)."""
    target_id = _insert_user(f"noenroll-{uuid.uuid4().hex[:6]}@ex.com")
    resp = auth_client.post(f"/api/admin/users/{target_id}/reset-2fa")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["had_factor"] is False
    assert body["backup_codes_cleared"] == 0
    assert _count_login_attempts(target_id, "admin_reset_2fa") == 1


def test_reset_2fa_404_for_unknown_user(auth_client: TestClient):
    resp = auth_client.post("/api/admin/users/nonexistent-id/reset-2fa")
    assert resp.status_code == 404


def test_reset_2fa_denied_without_permission():
    """A user with users.manage but NOT admin.users.reset_2fa gets 403.

    Builds a synthetic user with a profile that has users.manage only; the
    require_permission(USERS_RESET_2FA) gate rejects them.
    """
    from app.database import get_db
    from app.main import app as _app
    from app.services.auth import get_current_user as _gcu
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    # Build in-memory profile with ONLY users.manage
    profile = Profile(
        id=str(uuid.uuid4()),
        name="partial",
        description="partial-admin (test)",
        is_system=False,
    )
    profile.permissions = [
        ProfilePermission(profile_id=profile.id, permission_key=USERS_MANAGE),
    ]
    user = User(
        id=str(uuid.uuid4()),
        email=f"partial-{uuid.uuid4().hex[:6]}@ex.com",
        hashed_password="x",
        is_admin=False,
        status="active",
        profile_id=profile.id,
    )
    user.profile = profile

    async def _override_user():
        return user

    async def _override_db():
        async with _TestSession() as session:
            yield session

    _app.dependency_overrides[_gcu] = _override_user
    _app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(_app, raise_server_exceptions=False) as c:
            resp = c.post("/api/admin/users/any-id/reset-2fa")
        assert resp.status_code == 403, resp.text
        detail = resp.json()["detail"]
        assert detail["required_permission"] == "admin.users.reset_2fa"
    finally:
        _app.dependency_overrides.pop(_gcu, None)
        _app.dependency_overrides.pop(get_db, None)


# ── CLI `admin-recover` 2FA reset tests ──────────────────────────────────────


def test_cli_admin_recover_clears_2fa_by_default(monkeypatch, capsys):
    """`admin-recover <email>` with default behaviour wipes TOTP + codes."""
    from app import cli as cli_pkg  # noqa: F401 — ensures package is loaded
    from app.cli import commands as cli_commands
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    # Route the CLI's AsyncSessionLocal to the test DB session
    monkeypatch.setattr(cli_commands, "AsyncSessionLocal", _TestSession)

    email = f"cli-recover-{uuid.uuid4().hex[:6]}@ex.com"
    uid = _insert_user(email, is_admin=True)
    _seed_totp_and_codes(uid, num_codes=10)
    assert _count_totp(uid) == 1
    assert _count_backup_codes(uid) == 10

    cli_commands.cmd_admin_recover(email)  # reset_2fa default True

    assert _count_totp(uid) == 0
    assert _count_backup_codes(uid) == 0


def test_cli_admin_recover_keep_2fa_preserves_factor(monkeypatch, capsys):
    """`admin-recover --keep-2fa` preserves the user's TOTP factor + codes."""
    from app.cli import commands as cli_commands
    from tests.conftest import _TestSession  # type: ignore[attr-defined]

    monkeypatch.setattr(cli_commands, "AsyncSessionLocal", _TestSession)

    email = f"cli-keep-{uuid.uuid4().hex[:6]}@ex.com"
    uid = _insert_user(email, is_admin=True)
    _seed_totp_and_codes(uid, num_codes=10)

    cli_commands.cmd_admin_recover(email, reset_2fa=False)

    assert _count_totp(uid) == 1
    assert _count_backup_codes(uid) == 10
