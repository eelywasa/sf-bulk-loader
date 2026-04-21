"""Tests for email-related Settings fields after SFBL-155 migration.

After SFBL-155, email settings are managed via DB-backed SettingsService.
Only the distribution-profile email_backend default and the Settings class
structure are tested here. The email invariant validators have moved to
runtime validation in the email backend factory.

Retained tests:
- Distribution profile email_backend defaults
- email_backend field still exists and is set by profile
"""

import os

import pytest
from cryptography.fernet import Fernet

# Ensure a valid encryption key is present before importing Settings
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())

from app.config import Settings  # noqa: E402


SQLITE_URL = "sqlite+aiosqlite:////data/db/test.db"
PG_URL = "postgresql+asyncpg://user:pass@localhost/testdb"


def make(**kwargs) -> Settings:
    """Construct Settings with test-safe required fields plus overrides."""
    base = {
        "encryption_key": Fernet.generate_key().decode(),
        "jwt_secret_key": "test-secret",
        "database_url": SQLITE_URL,
    }
    base.update(kwargs)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Distribution profile email_backend defaults
# ---------------------------------------------------------------------------


def test_desktop_profile_defaults_email_backend_to_noop():
    s = make(app_distribution="desktop")
    assert s.email_backend == "noop"


def test_self_hosted_profile_defaults_email_backend_to_noop():
    s = make(app_distribution="self_hosted")
    assert s.email_backend == "noop"


def test_aws_hosted_profile_defaults_email_backend_to_ses():
    s = make(app_distribution="aws_hosted", database_url=PG_URL)
    assert s.email_backend == "ses"


def test_explicit_email_backend_overrides_profile_default():
    """Explicitly setting email_backend must take precedence over profile default."""
    s = make(
        app_distribution="self_hosted",
        email_backend="smtp",
    )
    assert s.email_backend == "smtp"


# ---------------------------------------------------------------------------
# Verify removed fields are no longer in Settings
# ---------------------------------------------------------------------------


def test_email_smtp_fields_removed_from_settings():
    """SMTP fields must not exist on Settings — they live in SettingsService now."""
    s = make()
    assert not hasattr(s, "email_smtp_host"), "email_smtp_host should be removed from Settings"
    assert not hasattr(s, "email_smtp_password"), "email_smtp_password should be removed from Settings"
    assert not hasattr(s, "email_smtp_port"), "email_smtp_port should be removed from Settings"


def test_frontend_base_url_removed_from_settings():
    """frontend_base_url must not exist on Settings — it lives in SettingsService now."""
    s = make()
    assert not hasattr(s, "frontend_base_url"), "frontend_base_url should be removed from Settings"


def test_email_timing_fields_removed_from_settings():
    """Timing/retry email fields must not exist on Settings — they live in SettingsService now."""
    s = make()
    assert not hasattr(s, "email_max_retries"), "email_max_retries should be removed from Settings"
    assert not hasattr(s, "email_retry_backoff_seconds"), "email_retry_backoff_seconds should be removed"
    assert not hasattr(s, "email_claim_lease_seconds"), "email_claim_lease_seconds should be removed"
