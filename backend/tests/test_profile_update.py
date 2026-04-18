"""Tests for PUT /api/me/profile (SFBL-148 — profile update)."""

import uuid

import pytest

from app.models.user import User
from app.services.auth import create_access_token, hash_password

from tests.conftest import _TestSession, _run_async


# ── Helpers ───────────────────────────────────────────────────────────────────


def _seed_user(
    *,
    username: str = "alice",
    display_name: str | None = None,
    role: str = "user",
    is_active: bool = True,
) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        hashed_password=hash_password("OldP4ss!Secure#"),
        display_name=display_name,
        role=role,
        is_active=is_active,
    )

    async def _insert():
        async with _TestSession() as session:
            session.add(user)
            await session.commit()

    _run_async(_insert())
    return user


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_update_display_name_valid(client):
    """Valid display_name is persisted and returned in response."""
    user = _seed_user()
    token = create_access_token(user)

    resp = client.put(
        "/api/me/profile",
        json={"display_name": "Alice Smith"},
        headers=_bearer(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["display_name"] == "Alice Smith"

    # Verify persisted in DB
    from sqlalchemy import select as sa_select

    async def _check():
        async with _TestSession() as session:
            result = await session.execute(sa_select(User).where(User.id == user.id))
            db_user = result.scalar_one()
            assert db_user.display_name == "Alice Smith"

    _run_async(_check())


def test_update_display_name_trims_whitespace(client):
    """Surrounding whitespace is trimmed before persisting."""
    user = _seed_user()
    token = create_access_token(user)

    resp = client.put(
        "/api/me/profile",
        json={"display_name": "  Bob  "},
        headers=_bearer(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "Bob"


def test_update_display_name_empty_string_returns_400(client):
    """Empty string display_name → 400."""
    user = _seed_user()
    token = create_access_token(user)

    resp = client.put(
        "/api/me/profile",
        json={"display_name": ""},
        headers=_bearer(token),
    )
    assert resp.status_code == 400


def test_update_display_name_whitespace_only_returns_400(client):
    """Whitespace-only display_name → 400."""
    user = _seed_user()
    token = create_access_token(user)

    resp = client.put(
        "/api/me/profile",
        json={"display_name": "   "},
        headers=_bearer(token),
    )
    assert resp.status_code == 400


def test_update_display_name_overlong_returns_400(client):
    """display_name > 120 chars → 400."""
    user = _seed_user()
    token = create_access_token(user)

    resp = client.put(
        "/api/me/profile",
        json={"display_name": "A" * 121},
        headers=_bearer(token),
    )
    assert resp.status_code == 400


def test_update_display_name_exactly_120_chars_accepted(client):
    """display_name of exactly 120 chars → 200."""
    user = _seed_user()
    token = create_access_token(user)

    resp = client.put(
        "/api/me/profile",
        json={"display_name": "A" * 120},
        headers=_bearer(token),
    )
    assert resp.status_code == 200


def test_update_profile_no_token_returns_401(client):
    """Anonymous request → 401."""
    resp = client.put(
        "/api/me/profile",
        json={"display_name": "Alice"},
    )
    assert resp.status_code == 401
