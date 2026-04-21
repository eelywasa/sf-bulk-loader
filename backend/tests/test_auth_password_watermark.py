"""Tests for the JWT password-watermark mechanism (SFBL-145).

The watermark ensures that any JWT whose ``iat`` (issued-at) timestamp is
strictly earlier than ``user.password_changed_at`` is rejected, even if the
token is otherwise cryptographically valid and unexpired.
"""

import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.config import settings
from app.models.user import User
from app.services.auth import (
    PasswordPolicyError,
    create_access_token,
    hash_password,
    validate_password_strength,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_user(**kwargs) -> User:
    # role kwarg dropped in migration 0022 — convert to is_admin for compat.
    role = kwargs.pop("role", None)
    if role == "admin" and "is_admin" not in kwargs:
        kwargs["is_admin"] = True
    defaults = dict(
        id=str(uuid.uuid4()),
        username="testuser",
        hashed_password=hash_password("Str0ng&Secure#Pass"),
        status="active",
        password_changed_at=None,
    )
    defaults.update(kwargs)
    return User(**defaults)


def _payload_with_iat(user: User, iat: int) -> dict:
    """Build a decoded JWT payload dict with a specific iat value."""
    exp = iat + settings.jwt_expiry_minutes * 60
    return {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "iat": iat,
        "exp": exp,
    }


# ── Watermark: get_current_user ───────────────────────────────────────────────
# We patch decode_access_token to return a controlled payload so tests are not
# affected by JWT expiry (which would fire before the watermark check).


@pytest.mark.asyncio
async def test_auth_rejects_token_issued_before_password_change():
    """A token whose iat predates password_changed_at must be rejected (HTTP 401)."""
    # password_changed_at is 1000 seconds in the future from epoch perspective;
    # iat is 500 — strictly less than password_changed_at timestamp.
    now_ts = int(time.time())
    pca_ts = now_ts + 1000
    iat_ts = now_ts + 500  # before pca_ts

    pca = datetime.fromtimestamp(pca_ts, tz=timezone.utc)
    user = _make_user(password_changed_at=pca)

    payload = _payload_with_iat(user, iat=iat_ts)

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=user)

    from fastapi.security import HTTPAuthorizationCredentials
    from app.services.auth import get_current_user

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake.token.here")

    with patch("app.services.auth.decode_access_token", return_value=payload):
        with patch("app.services.auth.settings") as mock_settings:
            mock_settings.auth_mode = "local"

            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(credentials=creds, db=mock_db)

            assert exc_info.value.status_code == 401
            assert "password change" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_auth_accepts_token_issued_after_password_change():
    """A token whose iat is > password_changed_at must be accepted."""
    now_ts = int(time.time())
    pca_ts = now_ts - 500  # password changed 500 seconds ago
    iat_ts = now_ts - 100  # token issued 100 seconds ago (after password change)

    pca = datetime.fromtimestamp(pca_ts, tz=timezone.utc)
    user = _make_user(password_changed_at=pca)

    payload = _payload_with_iat(user, iat=iat_ts)

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=user)

    from fastapi.security import HTTPAuthorizationCredentials
    from app.services.auth import get_current_user

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake.token.here")

    with patch("app.services.auth.decode_access_token", return_value=payload):
        with patch("app.services.auth.settings") as mock_settings:
            mock_settings.auth_mode = "local"

            result = await get_current_user(credentials=creds, db=mock_db)
            assert result is user


@pytest.mark.asyncio
async def test_auth_accepts_token_when_no_watermark():
    """When password_changed_at is None, any valid token is accepted."""
    user = _make_user(password_changed_at=None)
    now_ts = int(time.time())
    payload = _payload_with_iat(user, iat=now_ts)

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=user)

    from fastapi.security import HTTPAuthorizationCredentials
    from app.services.auth import get_current_user

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake.token.here")

    with patch("app.services.auth.decode_access_token", return_value=payload):
        with patch("app.services.auth.settings") as mock_settings:
            mock_settings.auth_mode = "local"

            result = await get_current_user(credentials=creds, db=mock_db)
            assert result is user


@pytest.mark.asyncio
async def test_auth_accepts_token_issued_exactly_at_watermark():
    """A token with iat == password_changed_at (not strictly less than) is accepted."""
    now_ts = int(time.time())
    pca_ts = now_ts - 500
    iat_ts = pca_ts  # exactly equal

    pca = datetime.fromtimestamp(pca_ts, tz=timezone.utc)
    user = _make_user(password_changed_at=pca)

    payload = _payload_with_iat(user, iat=iat_ts)

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=user)

    from fastapi.security import HTTPAuthorizationCredentials
    from app.services.auth import get_current_user

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake.token.here")

    with patch("app.services.auth.decode_access_token", return_value=payload):
        with patch("app.services.auth.settings") as mock_settings:
            mock_settings.auth_mode = "local"

            result = await get_current_user(credentials=creds, db=mock_db)
            assert result is user


@pytest.mark.asyncio
async def test_auth_handles_naive_password_changed_at():
    """password_changed_at without tzinfo (naive UTC) is treated as UTC."""
    now_ts = int(time.time())
    pca_ts = now_ts + 1000
    iat_ts = now_ts + 500  # before pca_ts

    # Naive datetime — no timezone attached
    pca_naive = datetime.utcfromtimestamp(pca_ts)  # naive
    user = _make_user(password_changed_at=pca_naive)

    payload = _payload_with_iat(user, iat=iat_ts)

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=user)

    from fastapi.security import HTTPAuthorizationCredentials
    from app.services.auth import get_current_user

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake.token.here")

    with patch("app.services.auth.decode_access_token", return_value=payload):
        with patch("app.services.auth.settings") as mock_settings:
            mock_settings.auth_mode = "local"

            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(credentials=creds, db=mock_db)

            assert exc_info.value.status_code == 401


# ── validate_password_strength ────────────────────────────────────────────────


class TestValidatePasswordStrength:
    def test_accepts_strong_password(self):
        # Should not raise
        validate_password_strength("Str0ng&Secure#Pass")

    def test_rejects_too_short(self):
        with pytest.raises(PasswordPolicyError) as exc_info:
            validate_password_strength("Sh0rt!")
        assert "at least 12 characters" in str(exc_info.value)

    def test_rejects_no_uppercase(self):
        with pytest.raises(PasswordPolicyError) as exc_info:
            validate_password_strength("nouppercase1!")
        assert "at least one uppercase letter" in str(exc_info.value)

    def test_rejects_no_lowercase(self):
        with pytest.raises(PasswordPolicyError) as exc_info:
            validate_password_strength("NOLOWERCASE1!")
        assert "at least one lowercase letter" in str(exc_info.value)

    def test_rejects_no_digit(self):
        with pytest.raises(PasswordPolicyError) as exc_info:
            validate_password_strength("NoDigitsHere!x")
        assert "at least one digit" in str(exc_info.value)

    def test_rejects_no_special(self):
        with pytest.raises(PasswordPolicyError) as exc_info:
            validate_password_strength("NoSpecialChar1A")
        assert "at least one special character" in str(exc_info.value)

    def test_reports_all_failures(self):
        """A password that violates multiple rules lists every failure."""
        with pytest.raises(PasswordPolicyError) as exc_info:
            validate_password_strength("weak")
        err = exc_info.value
        assert "at least 12 characters" in str(err)
        assert "at least one uppercase letter" in str(err)
        assert "at least one digit" in str(err)
        assert "at least one special character" in str(err)
        # failures attribute carries the list
        assert len(err.failures) >= 4

    def test_password_policy_error_is_value_error(self):
        """PasswordPolicyError must be catchable as ValueError."""
        with pytest.raises(ValueError):
            validate_password_strength("weak")

    def test_boundary_exactly_12_chars(self):
        """Exactly 12 characters passes the length check (other rules permitting)."""
        # This password is exactly 12 chars and meets all rules
        validate_password_strength("Abcdef1234!@")

    def test_boundary_11_chars_fails(self):
        """11 characters fails the length check."""
        with pytest.raises(PasswordPolicyError):
            validate_password_strength("Abcdef123!@")
