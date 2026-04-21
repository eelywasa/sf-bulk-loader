"""Tests for the break-glass CLI (SFBL-193).

Commands are tested by invoking the internal async implementations directly.
The public ``cmd_*`` wrappers call ``asyncio.run()`` which closes the event
loop — incompatible with pytest-asyncio's session loop — so tests drive the
``_do_*`` coroutines instead. SystemExit raising behaviour is preserved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.cli.commands import _do_admin_recover, _do_list_admins, _do_unlock
from app.database import AsyncSessionLocal
from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.services.auth import hash_password, verify_password


# ── helpers ─────────────────────────────────────────────────────────────────


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


async def test_admin_recover_active_admin(capsys):
    user = await _create_user(username="recover@example.com", status="active")

    await _do_admin_recover("recover@example.com")

    captured = capsys.readouterr()
    assert "Temporary password:" in captured.out
    assert "BREAK-GLASS ADMIN RECOVERY" in captured.out

    updated = await _get_user(user.id)
    assert updated.must_reset_password is True
    assert updated.locked_until is None
    assert updated.failed_login_count == 0
    assert updated.status == "active"

    assert await _count_login_attempts(user.id) == 1


async def test_admin_recover_locked_admin_becomes_active(capsys):
    user = await _create_user(username="locked@example.com", status="locked")

    await _do_admin_recover("locked@example.com")

    updated = await _get_user(user.id)
    assert updated.status == "active"
    assert updated.locked_until is None
    assert updated.failed_login_count == 0


async def test_admin_recover_invited_admin_becomes_active(capsys):
    user = await _create_user(username="invited@example.com", status="invited")

    await _do_admin_recover("invited@example.com")

    updated = await _get_user(user.id)
    assert updated.status == "active"


async def test_admin_recover_temp_password_is_valid(capsys):
    await _create_user(username="verify@example.com", status="active")

    await _do_admin_recover("verify@example.com")

    captured = capsys.readouterr()
    temp_pw = None
    for line in captured.out.splitlines():
        if "Temporary password:" in line:
            temp_pw = line.split("Temporary password:")[1].strip()
            break
    assert temp_pw, "Temp password not found in output"

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.username == "verify@example.com")
        )
        user = result.scalar_one()
    assert verify_password(temp_pw, user.hashed_password)


# ── admin-recover: refusal paths ─────────────────────────────────────────────


async def test_admin_recover_user_not_found_exits_2():
    with pytest.raises(SystemExit) as exc_info:
        await _do_admin_recover("nobody@example.com")
    assert exc_info.value.code == 2


async def test_admin_recover_non_admin_exits_3():
    await _create_user(username="regular@example.com", role="user", status="active")

    with pytest.raises(SystemExit) as exc_info:
        await _do_admin_recover("regular@example.com")
    assert exc_info.value.code == 3


async def test_admin_recover_deleted_admin_exits_4():
    await _create_user(username="deleted@example.com", role="admin", status="deleted")

    with pytest.raises(SystemExit) as exc_info:
        await _do_admin_recover("deleted@example.com")
    assert exc_info.value.code == 4


# ── unlock: success paths ─────────────────────────────────────────────────────


async def test_unlock_locked_user(capsys):
    user = await _create_user(username="lockeduser@example.com", status="locked")

    await _do_unlock("lockeduser@example.com")

    updated = await _get_user(user.id)
    assert updated.status == "active"
    assert updated.locked_until is None
    assert updated.failed_login_count == 0

    captured = capsys.readouterr()
    assert "unlocked successfully" in captured.out


async def test_unlock_active_user_stays_active(capsys):
    async with AsyncSessionLocal() as session:
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
        user_id = user.id

    await _do_unlock("activelocked@example.com")

    updated = await _get_user(user_id)
    assert updated.status == "active"
    assert updated.locked_until is None
    assert updated.failed_login_count == 0


async def test_unlock_works_on_any_role(capsys):
    user = await _create_user(username="anyuser@example.com", role="user", status="locked")

    await _do_unlock("anyuser@example.com")

    updated = await _get_user(user.id)
    assert updated.status == "active"


# ── unlock: refusal paths ─────────────────────────────────────────────────────


async def test_unlock_user_not_found_exits_2():
    with pytest.raises(SystemExit) as exc_info:
        await _do_unlock("ghost@example.com")
    assert exc_info.value.code == 2


# ── list-admins ───────────────────────────────────────────────────────────────


async def test_list_admins_empty(capsys):
    await _do_list_admins()
    captured = capsys.readouterr()
    assert "No admin users found" in captured.out


async def test_list_admins_shows_admin_users(capsys):
    await _create_user(username="admin1@example.com", role="admin", status="active")
    await _create_user(username="admin2@example.com", role="admin", status="locked")
    await _create_user(username="normaluser@example.com", role="user", status="active")

    await _do_list_admins()

    captured = capsys.readouterr()
    assert "admin1@example.com" in captured.out
    assert "admin2@example.com" in captured.out
    assert "normaluser@example.com" not in captured.out


async def test_list_admins_shows_locked_status(capsys):
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

    await _do_list_admins()

    captured = capsys.readouterr()
    assert "lockedadmin@example.com" in captured.out
    assert "YES" in captured.out
