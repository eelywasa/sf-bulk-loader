"""Tests for SFBL-155: email configuration migrated to DB-backed settings.

Covers:
- Registry contains all expected email keys with correct types and secret flags
- SMTP backend factory raises RuntimeError when email_backend=smtp but
  email_smtp_password is empty
- seed_from_env picks up EMAIL_* env vars correctly for email keys
"""

from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete

os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("JWT_SECRET_KEY", "migration-test-jwt")
os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "Test-Admin-P4ss!")

from app.services.settings.registry import SETTINGS_REGISTRY  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Registry completeness
# ---------------------------------------------------------------------------

_EXPECTED_EMAIL_KEYS: dict[str, dict] = {
    "email_backend":                    {"type": "str",   "is_secret": False, "category": "email"},
    "email_from_address":               {"type": "str",   "is_secret": False, "category": "email"},
    "email_from_name":                  {"type": "str",   "is_secret": False, "category": "email"},
    "email_reply_to":                   {"type": "str",   "is_secret": False, "category": "email"},
    "email_max_retries":                {"type": "int",   "is_secret": False, "category": "email"},
    "email_retry_backoff_seconds":      {"type": "float", "is_secret": False, "category": "email"},
    "email_retry_backoff_max_seconds":  {"type": "float", "is_secret": False, "category": "email"},
    "email_timeout_seconds":            {"type": "float", "is_secret": False, "category": "email"},
    "email_claim_lease_seconds":        {"type": "int",   "is_secret": False, "category": "email"},
    "email_pending_stale_minutes":      {"type": "int",   "is_secret": False, "category": "email"},
    "email_log_recipients":             {"type": "bool",  "is_secret": False, "category": "email"},
    "email_smtp_host":                  {"type": "str",   "is_secret": False, "category": "email"},
    "email_smtp_port":                  {"type": "int",   "is_secret": False, "category": "email"},
    "email_smtp_username":              {"type": "str",   "is_secret": False, "category": "email"},
    "email_smtp_password":              {"type": "str",   "is_secret": True,  "category": "email"},
    "email_smtp_starttls":              {"type": "bool",  "is_secret": False, "category": "email"},
    "email_smtp_use_tls":               {"type": "bool",  "is_secret": False, "category": "email"},
    "email_ses_region":                 {"type": "str",   "is_secret": False, "category": "email"},
    "email_ses_configuration_set":      {"type": "str",   "is_secret": False, "category": "email"},
    "frontend_base_url":                {"type": "str",   "is_secret": False, "category": "email"},
}


@pytest.mark.parametrize("key,attrs", _EXPECTED_EMAIL_KEYS.items())
def test_email_key_in_registry(key: str, attrs: dict) -> None:
    """Each expected email key must be registered with the correct type and secret flag."""
    assert key in SETTINGS_REGISTRY, f"Key {key!r} missing from SETTINGS_REGISTRY"
    meta = SETTINGS_REGISTRY[key]
    assert meta.type == attrs["type"], f"{key}: expected type={attrs['type']!r}, got {meta.type!r}"
    assert meta.is_secret == attrs["is_secret"], (
        f"{key}: expected is_secret={attrs['is_secret']}, got {meta.is_secret}"
    )
    assert meta.category == attrs["category"], (
        f"{key}: expected category={attrs['category']!r}, got {meta.category!r}"
    )


def test_email_smtp_password_is_secret() -> None:
    """email_smtp_password must have is_secret=True."""
    meta = SETTINGS_REGISTRY["email_smtp_password"]
    assert meta.is_secret is True


def test_all_email_keys_have_env_var() -> None:
    """All email keys should have an env_var set for seed_from_env to work."""
    for key, meta in SETTINGS_REGISTRY.items():
        if meta.category == "email":
            assert meta.env_var, f"Key {key!r} has no env_var — seed_from_env cannot seed it"


def test_all_email_keys_have_description() -> None:
    """All email keys should have a non-empty description for the admin UI."""
    for key, meta in SETTINGS_REGISTRY.items():
        if meta.category == "email":
            assert meta.description, f"Key {key!r} has no description"


# ---------------------------------------------------------------------------
# 2. SMTP backend raises when password is empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smtp_backend_raises_when_password_empty() -> None:
    """SmtpBackend.send() must raise RuntimeError when email_smtp_password is empty."""
    import app.services.settings.service as _svc_module
    from app.services.email.backends.smtp import SmtpBackend
    from app.services.email.message import EmailMessage

    class _NoPasswordSvc:
        async def get(self, key: str) -> object:
            return {"email_smtp_password": ""}.get(key, "")

    original = _svc_module.settings_service
    _svc_module.settings_service = _NoPasswordSvc()  # type: ignore[assignment]
    try:
        backend = SmtpBackend()
        msg = EmailMessage(to="a@b.com", subject="test", text_body="hi")
        with pytest.raises(RuntimeError, match="email_smtp_password"):
            await backend.send(msg)
    finally:
        _svc_module.settings_service = original


@pytest.mark.asyncio
async def test_smtp_backend_proceeds_when_password_set() -> None:
    """SmtpBackend.send() must NOT raise when email_smtp_password is non-empty."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    import app.services.settings.service as _svc_module
    from app.services.email.backends.smtp import SmtpBackend
    from app.services.email.message import EmailMessage

    class _WithPasswordSvc:
        async def get(self, key: str) -> object:
            settings = {
                "email_smtp_password": "secret123",
                "email_smtp_host": "localhost",
                "email_smtp_port": 587,
                "email_smtp_username": "",
                "email_smtp_starttls": False,
                "email_smtp_use_tls": False,
                "email_from_address": "from@example.com",
                "email_from_name": "",
                "email_timeout_seconds": 5.0,
            }
            return settings.get(key, "")

    original = _svc_module.settings_service
    _svc_module.settings_service = _WithPasswordSvc()  # type: ignore[assignment]
    try:
        backend = SmtpBackend()
        msg = EmailMessage(to="a@b.com", subject="test", text_body="hi")

        # Mock the SMTP context manager to avoid a real connection attempt
        fake_smtp = AsyncMock()
        fake_smtp.__aenter__ = AsyncMock(return_value=fake_smtp)
        fake_smtp.__aexit__ = AsyncMock(return_value=False)
        fake_smtp.send_message = AsyncMock(return_value=({"a@b.com": (250, "Ok")}, "Ok queued-id"))

        with patch("app.services.email.backends.smtp.aiosmtplib.SMTP", return_value=fake_smtp):
            result = await backend.send(msg)

        assert result["accepted"] is True
    finally:
        _svc_module.settings_service = original


# ---------------------------------------------------------------------------
# 3. seed_from_env picks up EMAIL_* env vars
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_from_env_reads_email_smtp_host() -> None:
    """seed_from_env must write EMAIL_SMTP_HOST env var into the DB."""
    from sqlalchemy import delete as _delete
    from app.models.app_setting import AppSetting
    from app.services.settings.service import SettingsService
    from tests.conftest import _TestSession

    # Clean the app_settings table first
    async with _TestSession() as s:
        await s.execute(_delete(AppSetting))
        await s.commit()

    original_env = os.environ.get("EMAIL_SMTP_HOST")
    os.environ["EMAIL_SMTP_HOST"] = "smtp.test.example.com"
    try:
        svc = SettingsService(_TestSession)
        await svc.seed_from_env()
        result = await svc.get("email_smtp_host")
        assert result == "smtp.test.example.com"
    finally:
        if original_env is None:
            os.environ.pop("EMAIL_SMTP_HOST", None)
        else:
            os.environ["EMAIL_SMTP_HOST"] = original_env
        async with _TestSession() as s:
            await s.execute(_delete(AppSetting))
            await s.commit()


@pytest.mark.asyncio
async def test_seed_from_env_encrypts_smtp_password() -> None:
    """seed_from_env must store email_smtp_password encrypted."""
    from sqlalchemy import delete as _delete
    from app.models.app_setting import AppSetting
    from app.services.settings.service import SettingsService
    from tests.conftest import _TestSession

    async with _TestSession() as s:
        await s.execute(_delete(AppSetting))
        await s.commit()

    original_env = os.environ.get("EMAIL_SMTP_PASSWORD")
    os.environ["EMAIL_SMTP_PASSWORD"] = "super-secret-pass"
    try:
        svc = SettingsService(_TestSession)
        await svc.seed_from_env()

        # Verify the DB row has is_encrypted=True
        async with _TestSession() as s:
            row = await s.get(AppSetting, "email_smtp_password")
            assert row is not None
            assert row.is_encrypted is True
            # Raw stored value must NOT equal the plaintext password
            assert row.value != "super-secret-pass"

        # But get() must return the plaintext
        result = await svc.get("email_smtp_password")
        assert result == "super-secret-pass"
    finally:
        if original_env is None:
            os.environ.pop("EMAIL_SMTP_PASSWORD", None)
        else:
            os.environ["EMAIL_SMTP_PASSWORD"] = original_env
        async with _TestSession() as s:
            await s.execute(_delete(AppSetting))
            await s.commit()
