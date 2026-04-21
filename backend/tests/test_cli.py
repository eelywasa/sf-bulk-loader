"""Tests for the break-glass CLI (SFBL-193).

Commands are tested via direct import (fast) rather than subprocess to avoid
spinning up a second process.  Each test seeds a User record, calls the
command function, and then asserts on DB state or exit behaviour.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import select

# ── env isolation (must run before any app import) ─────────────────────────
# conftest.py already sets SFBL_DISABLE_ENV_FILE and ENCRYPTION_KEY; this file
# piggy-backs on that setup automatically via the session-scoped fixture chain.

from app.cli.commands import cmd_admin_recover, cmd_list_admins, cmd_unlock
from app.database import AsyncSessionLocal
from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.services.auth import hash_password, verify_password


# ── helpers ─────────────────────────────────────────────────────────────────


def _run(coro):
    """Run a coroutine on a fresh event loop (safe outside pytest-asyncio)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _create_user(
    username: str = "admin@example.com",
    role: str = "admin",
    status: str = "active",
    password: str = "OldPassword1!",
) -> User:
    async with AsyncSessionLocal() as session:
        user = User(
            username=username,
            hashed_password=hash_password(password),
            email=username,
            role=role,
            status=status,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _get_user(user_id: str) -> User:
    async with AsyncSessionLocal() as session:
        return await session.get(User, user_id)


async def _count_login_attempts(user_id: str) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LoginAttempt).where(LoginAttempt.user_id == user_id)
        )
        return len(result.scalars().all())


# ── admin-recover: success paths ─────────────────────────────────────────────


def test_admin_recover_active_admin(capsys):
    """admin-recover should reset password and print temp password."""
    user = _run(_create_user(username="recover@example.com", status="active"))

    cmd_admin_recover("recover@example.com")

    captured = capsys.readouterr()
    assert "Temporary password:" in captured.out
    assert "BREAK-GLASS ADMIN RECOVERY" in captured.out

    updated = _run(_get_user(user.id))
    assert updated.must_reset_password is True
    assert updated.locked_until is None
    assert updated.failed_login_count == 0
    assert updated.status == "active"

    # LoginAttempt row written
    assert _run(_count_login_attempts(user.id)) == 1


def test_admin_recover_locked_admin_becomes_active(capsys):
    """admin-recover should transition a locked admin back to active."""
    user = _run(_create_user(username="locked@example.com", status="locked"))

    cmd_admin_recover("locked@example.com")

    updated = _run(_get_user(user.id))
    assert updated.status == "active"
    assert updated.locked_until is None
    assert updated.failed_login_count == 0


def test_admin_recover_invited_admin_becomes_active(capsys):
    """admin-recover should transition an invited admin to active."""
    user = _run(_create_user(username="invited@example.com", status="invited"))

    cmd_admin_recover("invited@example.com")

    updated = _run(_get_user(user.id))
    assert updated.status == "active"


def test_admin_recover_temp_password_is_valid(capsys):
    """The temp password printed to stdout must verify against the stored hash."""
    _run(_create_user(username="verify@example.com", status="active"))

    cmd_admin_recover("verify@example.com")

    captured = capsys.readouterr()
    # Extract the temp password from output
    for line in captured.out.splitlines():
        if "Temporary password:" in line:
            temp_pw = line.split("Temporary password:")[1].strip()
            break
    else:
        pytest.fail("Temp password not found in output")

    # Re-fetch user and verify
    async def _fetch():
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.username == "verify@example.com")
            )
            return result.scalar_one()

    user = _run(_fetch())
    assert verify_password(temp_pw, user.hashed_password)


# ── admin-recover: refusal paths ─────────────────────────────────────────────


def test_admin_recover_user_not_found_exits_2():
    """admin-recover should exit 2 when no user matches the email."""
    with pytest.raises(SystemExit) as exc_info:
        cmd_admin_recover("nobody@example.com")
    assert exc_info.value.code == 2


def test_admin_recover_non_admin_exits_3():
    """admin-recover should exit 3 when user is not an admin."""
    _run(_create_user(username="regular@example.com", role="user", status="active"))

    with pytest.raises(SystemExit) as exc_info:
        cmd_admin_recover("regular@example.com")
    assert exc_info.value.code == 3


def test_admin_recover_deleted_admin_exits_4():
    """admin-recover should exit 4 when the admin has status='deleted'."""
    _run(_create_user(username="deleted@example.com", role="admin", status="deleted"))

    with pytest.raises(SystemExit) as exc_info:
        cmd_admin_recover("deleted@example.com")
    assert exc_info.value.code == 4


# ── unlock: success paths ─────────────────────────────────────────────────────


def test_unlock_locked_user(capsys):
    """unlock should clear lockout and transition locked → active."""
    user = _run(_create_user(username="lockeduser@example.com", status="locked"))

    cmd_unlock("lockeduser@example.com")

    updated = _run(_get_user(user.id))
    assert updated.status == "active"
    assert updated.locked_until is None
    assert updated.failed_login_count == 0

    captured = capsys.readouterr()
    assert "unlocked successfully" in captured.out


def test_unlock_active_user_stays_active(capsys):
    """unlock on an active user (with lockout fields set) clears fields but keeps status."""
    from datetime import timedelta

    async def _create_with_lockout():
        async with AsyncSessionLocal() as session:
            from datetime import datetime, timezone
            user = User(
                username="activelocked@example.com",
                hashed_password=hash_password("Pass123!"),
                email="activelocked@example.com",
                role="user",
                status="active",
                failed_login_count=3,
                locked_until=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    user = _run(_create_with_lockout())
    cmd_unlock("activelocked@example.com")

    updated = _run(_get_user(user.id))
    assert updated.status == "active"
    assert updated.locked_until is None
    assert updated.failed_login_count == 0


def test_unlock_works_on_any_role(capsys):
    """unlock is not restricted to admins."""
    user = _run(_create_user(username="anyuser@example.com", role="user", status="locked"))

    cmd_unlock("anyuser@example.com")

    updated = _run(_get_user(user.id))
    assert updated.status == "active"


# ── unlock: refusal paths ─────────────────────────────────────────────────────


def test_unlock_user_not_found_exits_2():
    """unlock should exit 2 when no user matches the email."""
    with pytest.raises(SystemExit) as exc_info:
        cmd_unlock("ghost@example.com")
    assert exc_info.value.code == 2


# ── list-admins ───────────────────────────────────────────────────────────────


def test_list_admins_empty(capsys):
    """list-admins prints a friendly message when no admins exist."""
    cmd_list_admins()
    captured = capsys.readouterr()
    assert "No admin users found" in captured.out


def test_list_admins_shows_admin_users(capsys):
    """list-admins includes all admin users in the output."""
    _run(_create_user(username="admin1@example.com", role="admin", status="active"))
    _run(_create_user(username="admin2@example.com", role="admin", status="locked"))
    _run(_create_user(username="normaluser@example.com", role="user", status="active"))

    cmd_list_admins()

    captured = capsys.readouterr()
    assert "admin1@example.com" in captured.out
    assert "admin2@example.com" in captured.out
    # Non-admin users should not appear
    assert "normaluser@example.com" not in captured.out


def test_list_admins_shows_locked_status(capsys):
    """list-admins indicates when an admin is currently locked."""
    from datetime import datetime, timedelta, timezone

    async def _create_locked_admin():
        async with AsyncSessionLocal() as session:
            user = User(
                username="lockedadmin@example.com",
                hashed_password=hash_password("Pass123!"),
                email="lockedadmin@example.com",
                role="admin",
                status="active",
                locked_until=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
            session.add(user)
            await session.commit()

    _run(_create_locked_admin())

    cmd_list_admins()

    captured = capsys.readouterr()
    assert "lockedadmin@example.com" in captured.out
    assert "YES" in captured.out
