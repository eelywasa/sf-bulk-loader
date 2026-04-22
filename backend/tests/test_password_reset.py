"""Tests for unauthenticated password-reset flow (SFBL-147).

Covers:
- Request with known email → 202, delivery row created, PasswordResetToken exists.
- Request with unknown email → 202, no delivery, no token row.
- Per-IP rate-limit → 429 after N hits.
- Per-email rate-limit → 429 (different IPs, same email).
- Confirm with valid token + strong password → 204; hash updated; watermark bumped; token used.
- Confirm same token twice → second 400.
- Expired token → 400.
- Weak password → 400 with policy message.
- Confirm invalidates sibling unused tokens.
- SAML-only user → 400.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

import app.services.rate_limit as rl_module
from app.models.email_delivery import EmailDelivery
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.services.auth import hash_password

from tests.conftest import _TestSession, _run_async


# ── Constants ─────────────────────────────────────────────────────────────────

_STRONG_PASSWORD = "NewStr0ng!Pass#"
_WEAK_PASSWORD = "weak"
_EMAIL = "alice@example.com"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _seed_user(
    *,
    email: str = _EMAIL,
    hashed_pw: str | None = None,
    saml_only: bool = False,
    is_active: bool = True,
) -> User:
    """Insert a user with the given email and return the ORM object."""
    if saml_only:
        pw = None
    elif hashed_pw is not None:
        pw = hashed_pw
    else:
        pw = hash_password("OldP4ss!Secure#")

    user = User(
        id=str(uuid.uuid4()),
        username=f"user_{uuid.uuid4().hex[:6]}",
        email=email.lower(),
        display_name="Alice",
        hashed_password=pw,
        status="active" if is_active else "deactivated",
    )

    async def _insert():
        async with _TestSession() as session:
            session.add(user)
            await session.commit()

    _run_async(_insert())
    return user


def _seed_token(
    user_id: str,
    *,
    raw_token: str = "testrawtoken12345",
    used: bool = False,
    expired: bool = False,
) -> PasswordResetToken:
    """Insert a PasswordResetToken and return it."""
    now = datetime.now(timezone.utc)
    expires_at = now - timedelta(minutes=1) if expired else now + timedelta(minutes=15)
    used_at = now if used else None

    token_row = PasswordResetToken(
        id=str(uuid.uuid4()),
        user_id=user_id,
        token_hash=_sha256_hex(raw_token),
        expires_at=expires_at,
        created_at=now,
        used_at=used_at,
        request_ip="127.0.0.1",
    )

    async def _insert():
        async with _TestSession() as session:
            session.add(token_row)
            await session.commit()

    _run_async(_insert())
    return token_row


def _get_token_row(token_hash: str) -> PasswordResetToken | None:
    async def _fetch():
        async with _TestSession() as session:
            result = await session.execute(
                select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
            )
            return result.scalars().first()

    return _run_async(_fetch())


def _get_user(user_id: str) -> User | None:
    async def _fetch():
        async with _TestSession() as session:
            return await session.get(User, user_id)

    return _run_async(_fetch())


def _get_delivery_count() -> int:
    async def _fetch():
        async with _TestSession() as session:
            result = await session.execute(select(EmailDelivery))
            return len(result.scalars().all())

    return _run_async(_fetch())


def _get_token_count_for_user(user_id: str) -> int:
    async def _fetch():
        async with _TestSession() as session:
            result = await session.execute(
                select(PasswordResetToken).where(PasswordResetToken.user_id == user_id)
            )
            return len(result.scalars().all())

    return _run_async(_fetch())


def _clear_rate_limit():
    rl_module._store.clear()


# ── Request endpoint tests ────────────────────────────────────────────────────


class TestPasswordResetRequest:
    def setup_method(self):
        _clear_rate_limit()

    def test_known_email_returns_202(self, client):
        _seed_user()
        resp = client.post("/api/auth/password-reset/request", json={"email": _EMAIL})
        assert resp.status_code == 202

    def test_known_email_creates_token_row(self, client):
        _seed_user()
        resp = client.post("/api/auth/password-reset/request", json={"email": _EMAIL})
        assert resp.status_code == 202

        async def _count():
            async with _TestSession() as session:
                result = await session.execute(select(PasswordResetToken))
                return len(result.scalars().all())

        count = _run_async(_count())
        assert count == 1

    def test_known_email_creates_delivery_row(self, client):
        _seed_user()
        before = _get_delivery_count()
        client.post("/api/auth/password-reset/request", json={"email": _EMAIL})
        after = _get_delivery_count()
        assert after == before + 1

    def test_unknown_email_returns_202(self, client):
        resp = client.post(
            "/api/auth/password-reset/request", json={"email": "nobody@example.com"}
        )
        assert resp.status_code == 202

    def test_unknown_email_creates_no_token(self, client):
        client.post(
            "/api/auth/password-reset/request", json={"email": "nobody@example.com"}
        )

        async def _count():
            async with _TestSession() as session:
                result = await session.execute(select(PasswordResetToken))
                return len(result.scalars().all())

        assert _run_async(_count()) == 0

    def test_unknown_email_creates_no_delivery(self, client):
        before = _get_delivery_count()
        client.post(
            "/api/auth/password-reset/request", json={"email": "nobody@example.com"}
        )
        assert _get_delivery_count() == before

    def test_per_ip_rate_limit(self, client):
        """After N per-IP hits, the endpoint returns 429.

        TestClient uses 'testclient' as the client host.  We pre-fill the
        rate-limit store for that key up to the configured limit so the next
        real request is rejected without mutating settings.
        """
        import asyncio
        from app.config import settings
        from app.services.rate_limit import check_and_record

        # The TestClient sends requests from host "testclient"
        ip_rl_key = "rl:ip:testclient"
        limit = settings.pw_reset_rate_limit_per_ip_hour

        async def _fill():
            for _ in range(limit):
                await check_and_record(ip_rl_key, limit=limit, window_seconds=3600)

        asyncio.get_event_loop().run_until_complete(_fill())

        resp = client.post(
            "/api/auth/password-reset/request", json={"email": "iplimited@example.com"}
        )
        assert resp.status_code == 429

    def test_per_email_rate_limit(self, client):
        """Per-email limit triggers 429 even from different IPs.

        Pre-fill the email bucket up to the configured limit so the next
        request with that email is rejected regardless of IP.
        """
        import asyncio
        from app.config import settings
        from app.services.rate_limit import check_and_record, hashed_email_key

        email_key = hashed_email_key("ratelimited@example.com")
        limit = settings.pw_reset_rate_limit_per_email_hour

        async def _fill():
            for _ in range(limit):
                await check_and_record(email_key, limit=limit, window_seconds=3600)

        asyncio.get_event_loop().run_until_complete(_fill())

        resp = client.post(
            "/api/auth/password-reset/request",
            json={"email": "ratelimited@example.com"},
        )
        assert resp.status_code == 429


# ── Confirm endpoint tests ────────────────────────────────────────────────────


class TestPasswordResetConfirm:
    def setup_method(self):
        _clear_rate_limit()

    def test_valid_token_returns_204(self, client):
        user = _seed_user()
        raw = "validtoken12345678901234567890"
        _seed_token(user.id, raw_token=raw)

        resp = client.post(
            "/api/auth/password-reset/confirm",
            json={"token": raw, "new_password": _STRONG_PASSWORD},
        )
        assert resp.status_code == 204

    def test_valid_token_updates_password_hash(self, client):
        user = _seed_user()
        raw = "updatehashtoken1234567890abcd"
        _seed_token(user.id, raw_token=raw)

        old_hash = _get_user(user.id).hashed_password
        client.post(
            "/api/auth/password-reset/confirm",
            json={"token": raw, "new_password": _STRONG_PASSWORD},
        )
        new_hash = _get_user(user.id).hashed_password
        assert new_hash != old_hash

    def test_valid_token_bumps_password_changed_at(self, client):
        user = _seed_user()
        raw = "bumpwatermark12345678901234"
        _seed_token(user.id, raw_token=raw)

        before_user = _get_user(user.id)
        assert before_user.password_changed_at is None

        client.post(
            "/api/auth/password-reset/confirm",
            json={"token": raw, "new_password": _STRONG_PASSWORD},
        )

        after_user = _get_user(user.id)
        assert after_user.password_changed_at is not None

    def test_valid_token_marks_token_used(self, client):
        user = _seed_user()
        raw = "marktokenused1234567890abcde"
        token_row = _seed_token(user.id, raw_token=raw)

        client.post(
            "/api/auth/password-reset/confirm",
            json={"token": raw, "new_password": _STRONG_PASSWORD},
        )

        updated = _get_token_row(_sha256_hex(raw))
        assert updated is not None
        assert updated.used_at is not None

    def test_same_token_twice_returns_400(self, client):
        user = _seed_user()
        raw = "doublereset12345678901234567"
        _seed_token(user.id, raw_token=raw)

        resp1 = client.post(
            "/api/auth/password-reset/confirm",
            json={"token": raw, "new_password": _STRONG_PASSWORD},
        )
        assert resp1.status_code == 204

        resp2 = client.post(
            "/api/auth/password-reset/confirm",
            json={"token": raw, "new_password": _STRONG_PASSWORD},
        )
        assert resp2.status_code == 400

    def test_expired_token_returns_400(self, client):
        user = _seed_user()
        raw = "expiredtoken12345678901234ab"
        _seed_token(user.id, raw_token=raw, expired=True)

        resp = client.post(
            "/api/auth/password-reset/confirm",
            json={"token": raw, "new_password": _STRONG_PASSWORD},
        )
        assert resp.status_code == 400
        assert "expired" in resp.json()["detail"].lower() or "invalid" in resp.json()["detail"].lower()

    def test_weak_password_returns_400(self, client):
        user = _seed_user()
        raw = "weakpasswordtoken12345678901"
        _seed_token(user.id, raw_token=raw)

        resp = client.post(
            "/api/auth/password-reset/confirm",
            json={"token": raw, "new_password": _WEAK_PASSWORD},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "minimum requirements" in detail or "at least" in detail

    def test_invalid_token_returns_400(self, client):
        resp = client.post(
            "/api/auth/password-reset/confirm",
            json={"token": "notavalidtoken000000000000000", "new_password": _STRONG_PASSWORD},
        )
        assert resp.status_code == 400

    def test_saml_only_user_returns_400(self, client):
        user = _seed_user(saml_only=True)
        raw = "samlonlytoken123456789012345"
        _seed_token(user.id, raw_token=raw)

        resp = client.post(
            "/api/auth/password-reset/confirm",
            json={"token": raw, "new_password": _STRONG_PASSWORD},
        )
        assert resp.status_code == 400
        assert "not available" in resp.json()["detail"]

    def test_confirm_invalidates_sibling_tokens(self, client):
        """Confirming one token marks all other unused tokens for the same user as used."""
        user = _seed_user()
        raw_a = "siblingtokenA12345678901234"
        raw_b = "siblingtokenB12345678901234"
        _seed_token(user.id, raw_token=raw_a)
        token_b = _seed_token(user.id, raw_token=raw_b)

        # Confirm token A
        resp = client.post(
            "/api/auth/password-reset/confirm",
            json={"token": raw_a, "new_password": _STRONG_PASSWORD},
        )
        assert resp.status_code == 204

        # Token B (sibling) should now be used
        token_b_refreshed = _get_token_row(_sha256_hex(raw_b))
        assert token_b_refreshed is not None
        assert token_b_refreshed.used_at is not None

    def test_password_changed_at_truncated_to_seconds(self, client):
        """password_changed_at must have microsecond=0 (watermark pattern)."""
        user = _seed_user()
        raw = "truncatedsecondtoken123456789"
        _seed_token(user.id, raw_token=raw)

        client.post(
            "/api/auth/password-reset/confirm",
            json={"token": raw, "new_password": _STRONG_PASSWORD},
        )
        updated_user = _get_user(user.id)
        assert updated_user.password_changed_at is not None
        assert updated_user.password_changed_at.microsecond == 0
