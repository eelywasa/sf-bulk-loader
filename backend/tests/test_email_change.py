"""Tests for email-change endpoints (SFBL-148).

POST /api/me/email-change/request  — authenticated
POST /api/me/email-change/confirm  — public (token supplies identity)
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models.email_change_token import EmailChangeToken
from app.models.user import User
from app.services.auth import create_access_token, hash_password

from tests.conftest import _TestSession, _run_async


# ── Helpers ───────────────────────────────────────────────────────────────────


def _sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _seed_user(
    *,
    username: str = "alice",
    email: str | None = "alice@example.com",
    display_name: str | None = "Alice",
    role: str = "user",
    is_active: bool = True,
    hashed_password: str | None = None,
) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        email=email,
        display_name=display_name,
        hashed_password=hashed_password or hash_password("OldP4ss!Secure#"),
        role=role,
        is_active=is_active,
    )

    async def _insert():
        async with _TestSession() as session:
            session.add(user)
            await session.commit()

    _run_async(_insert())
    return user


def _seed_token(
    user_id: str,
    new_email: str = "new@example.com",
    raw_token: str | None = None,
    expires_at: datetime | None = None,
    used: bool = False,
) -> tuple[str, EmailChangeToken]:
    """Insert an EmailChangeToken and return (raw_token, record)."""
    if raw_token is None:
        raw_token = secrets.token_hex(32)
    token_hash = _sha256_hex(raw_token)
    now = datetime.now(timezone.utc)
    if expires_at is None:
        expires_at = now + timedelta(minutes=30)

    record = EmailChangeToken(
        user_id=user_id,
        token_hash=token_hash,
        new_email=new_email,
        expires_at=expires_at,
        created_at=now,
        used_at=now if used else None,
    )

    async def _insert():
        async with _TestSession() as session:
            session.add(record)
            await session.commit()

    _run_async(_insert())
    return raw_token, record


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _get_tokens_for_user(user_id: str) -> list[EmailChangeToken]:
    from sqlalchemy import select as sa_select

    async def _fetch():
        async with _TestSession() as session:
            result = await session.execute(
                sa_select(EmailChangeToken).where(EmailChangeToken.user_id == user_id)
            )
            return result.scalars().all()

    return _run_async(_fetch())


def _get_user(user_id: str) -> User | None:
    async def _fetch():
        async with _TestSession() as session:
            return await session.get(User, user_id)

    return _run_async(_fetch())


# ── Email delivery count helper ───────────────────────────────────────────────


def _count_deliveries() -> int:
    from sqlalchemy import func, select as sa_select
    from app.models.email_delivery import EmailDelivery

    async def _fetch():
        async with _TestSession() as session:
            result = await session.execute(sa_select(func.count()).select_from(EmailDelivery))
            return result.scalar_one()

    return _run_async(_fetch())


# ── POST /api/me/email-change/request ────────────────────────────────────────


def test_email_change_request_returns_202(client):
    """Valid request → 202 and token row created."""
    user = _seed_user()
    token = create_access_token(user)

    before = _count_deliveries()
    resp = client.post(
        "/api/me/email-change/request",
        json={"new_email": "new@example.com"},
        headers=_bearer(token),
    )
    assert resp.status_code == 202, resp.text

    # Token row created
    tokens = _get_tokens_for_user(user.id)
    assert len(tokens) == 1
    assert tokens[0].new_email == "new@example.com"
    assert tokens[0].used_at is None

    # Two delivery rows: verify (to new) + notice (to current)
    after = _count_deliveries()
    assert after - before == 2


def test_email_change_same_email_returns_400(client):
    """Requesting the same email as current → 400."""
    user = _seed_user(email="alice@example.com")
    token = create_access_token(user)

    resp = client.post(
        "/api/me/email-change/request",
        json={"new_email": "alice@example.com"},
        headers=_bearer(token),
    )
    assert resp.status_code == 400
    assert "unchanged" in resp.json()["detail"].lower()


def test_email_change_case_insensitive_same_email_returns_400(client):
    """Case-insensitive match for same email → 400."""
    user = _seed_user(email="alice@example.com")
    token = create_access_token(user)

    resp = client.post(
        "/api/me/email-change/request",
        json={"new_email": "ALICE@EXAMPLE.COM"},
        headers=_bearer(token),
    )
    assert resp.status_code == 400


def test_email_change_email_in_use_returns_400(client):
    """Email already used by another active user → 400."""
    user = _seed_user(username="alice", email="alice@example.com")
    _seed_user(username="bob", email="bob@example.com")
    token = create_access_token(user)

    resp = client.post(
        "/api/me/email-change/request",
        json={"new_email": "bob@example.com"},
        headers=_bearer(token),
    )
    assert resp.status_code == 400
    assert "in use" in resp.json()["detail"].lower()


def test_email_change_rate_limited(client):
    """Exceeding the rate limit → 429."""
    user = _seed_user()
    token = create_access_token(user)

    # Patch check_and_record to simulate limit hit
    import app.api.profile as profile_module
    original = profile_module.check_and_record

    call_count = {"n": 0}

    async def _limited(key, limit, window_seconds):
        call_count["n"] += 1
        # Fail on first call
        return False

    profile_module.check_and_record = _limited
    try:
        resp = client.post(
            "/api/me/email-change/request",
            json={"new_email": "new@example.com"},
            headers=_bearer(token),
        )
        assert resp.status_code == 429
    finally:
        profile_module.check_and_record = original


def test_email_change_invalidates_prior_pending_token(client):
    """New request marks prior pending token as used."""
    user = _seed_user()
    _, prior_token_record = _seed_token(user.id, new_email="old-new@example.com")
    prior_id = prior_token_record.id

    token = create_access_token(user)
    resp = client.post(
        "/api/me/email-change/request",
        json={"new_email": "brand-new@example.com"},
        headers=_bearer(token),
    )
    assert resp.status_code == 202, resp.text

    # Prior token must now be used
    from sqlalchemy import select as sa_select

    async def _check():
        async with _TestSession() as session:
            result = await session.execute(
                sa_select(EmailChangeToken).where(EmailChangeToken.id == prior_id)
            )
            return result.scalar_one()

    prior = _run_async(_check())
    assert prior.used_at is not None


def test_email_change_request_unauthenticated_returns_401(client):
    """No token → 401."""
    resp = client.post(
        "/api/me/email-change/request",
        json={"new_email": "new@example.com"},
    )
    assert resp.status_code == 401


# ── POST /api/me/email-change/confirm ────────────────────────────────────────


def test_email_change_confirm_valid_token_updates_email(client):
    """Valid token → 204, user.email updated, token marked used."""
    user = _seed_user(email="old@example.com")
    raw_token, token_record = _seed_token(user.id, new_email="new@example.com")

    resp = client.post(
        "/api/me/email-change/confirm",
        json={"token": raw_token},
    )
    assert resp.status_code == 204, resp.text

    # User email updated
    updated = _get_user(user.id)
    assert updated is not None
    assert updated.email == "new@example.com"

    # Token marked used
    from sqlalchemy import select as sa_select

    async def _check_token():
        async with _TestSession() as session:
            result = await session.execute(
                sa_select(EmailChangeToken).where(EmailChangeToken.id == token_record.id)
            )
            return result.scalar_one()

    used_token = _run_async(_check_token())
    assert used_token.used_at is not None


def test_email_change_confirm_twice_returns_400(client):
    """Confirming the same token twice → second attempt returns 400."""
    user = _seed_user(email="old@example.com")
    raw_token, _ = _seed_token(user.id, new_email="new@example.com")

    resp1 = client.post("/api/me/email-change/confirm", json={"token": raw_token})
    assert resp1.status_code == 204

    resp2 = client.post("/api/me/email-change/confirm", json={"token": raw_token})
    assert resp2.status_code == 400


def test_email_change_confirm_expired_token_returns_400(client):
    """Expired token → 400."""
    user = _seed_user(email="old@example.com")
    expired_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    raw_token, _ = _seed_token(user.id, new_email="new@example.com", expires_at=expired_at)

    resp = client.post("/api/me/email-change/confirm", json={"token": raw_token})
    assert resp.status_code == 400


def test_email_change_confirm_invalid_token_returns_400(client):
    """Nonexistent token → 400."""
    resp = client.post(
        "/api/me/email-change/confirm",
        json={"token": secrets.token_hex(32)},
    )
    assert resp.status_code == 400


def test_email_change_confirm_email_taken_at_confirm_time_returns_400(client):
    """Email taken by another user between request and confirm → 400; token left unused."""
    user = _seed_user(username="alice", email="alice@example.com")
    raw_token, token_record = _seed_token(user.id, new_email="taken@example.com")

    # Another user claims that email after token was created
    _seed_user(username="carol", email="taken@example.com")

    resp = client.post("/api/me/email-change/confirm", json={"token": raw_token})
    assert resp.status_code == 400
    assert "in use" in resp.json()["detail"].lower()

    # Token must remain unused (not consumed by the race)
    from sqlalchemy import select as sa_select

    async def _check_token():
        async with _TestSession() as session:
            result = await session.execute(
                sa_select(EmailChangeToken).where(EmailChangeToken.id == token_record.id)
            )
            return result.scalar_one()

    still_unused = _run_async(_check_token())
    assert still_unused.used_at is None


def test_email_change_confirm_does_not_bump_password_changed_at(client):
    """Confirming email change does NOT update password_changed_at."""
    user = _seed_user(email="old@example.com")
    original_pca = user.password_changed_at  # None by default
    raw_token, _ = _seed_token(user.id, new_email="new@example.com")

    resp = client.post("/api/me/email-change/confirm", json={"token": raw_token})
    assert resp.status_code == 204

    updated = _get_user(user.id)
    assert updated is not None
    assert updated.password_changed_at == original_pca
