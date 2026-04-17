"""Tests for email-related Settings fields, validators, and distribution profile defaults.

Covers:
- SMTP password resolution (env > file > error)
- Distribution profile email_backend defaults
- Invariant validators (lease, backoff, address)
"""

import os

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

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
# SMTP password resolution
# ---------------------------------------------------------------------------


def test_smtp_password_env_var_wins_over_file(tmp_path):
    """EMAIL_SMTP_PASSWORD env var takes precedence over EMAIL_SMTP_PASSWORD_FILE."""
    password_file = tmp_path / "smtp.pass"
    password_file.write_text("file-password\n")

    s = make(
        email_backend="smtp",
        email_from_address="sender@example.com",
        email_smtp_host="mail.example.com",
        email_smtp_password="env-password",
        email_smtp_password_file=str(password_file),
    )
    assert s.email_smtp_password == "env-password"


def test_smtp_password_file_used_when_env_empty(tmp_path):
    """When env var is empty/unset, file contents are used."""
    password_file = tmp_path / "smtp.pass"
    password_file.write_text("  file-password  \n")

    s = make(
        email_backend="smtp",
        email_from_address="sender@example.com",
        email_smtp_host="mail.example.com",
        email_smtp_password=None,
        email_smtp_password_file=str(password_file),
    )
    assert s.email_smtp_password == "file-password"


def test_smtp_backend_without_password_raises_value_error():
    """EMAIL_BACKEND=smtp with no password resolvable raises ValueError at boot."""
    with pytest.raises(ValidationError) as exc_info:
        make(
            email_backend="smtp",
            email_from_address="sender@example.com",
            email_smtp_host="mail.example.com",
            email_smtp_password=None,
            email_smtp_password_file=None,
        )
    # The error message must mention EMAIL_SMTP_PASSWORD
    assert "EMAIL_SMTP_PASSWORD" in str(exc_info.value)


def test_smtp_backend_missing_file_raises_value_error(tmp_path):
    """EMAIL_BACKEND=smtp with a *_FILE path that doesn't exist also fails."""
    nonexistent = tmp_path / "missing.pass"

    with pytest.raises(ValidationError) as exc_info:
        make(
            email_backend="smtp",
            email_from_address="sender@example.com",
            email_smtp_host="mail.example.com",
            email_smtp_password=None,
            email_smtp_password_file=str(nonexistent),
        )
    assert "EMAIL_SMTP_PASSWORD" in str(exc_info.value)


def test_noop_backend_without_smtp_password_is_fine():
    """noop backend does not require SMTP credentials."""
    s = make(
        email_backend="noop",
        email_smtp_password=None,
        email_smtp_password_file=None,
    )
    assert s.email_smtp_password == ""


def test_ses_backend_without_smtp_password_is_fine():
    """ses backend does not require SMTP credentials."""
    s = make(
        email_backend="ses",
        email_from_address="sender@example.com",
        email_smtp_password=None,
        email_smtp_password_file=None,
    )
    assert s.email_smtp_password == ""


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
        email_from_address="sender@example.com",
        email_smtp_host="mail.example.com",
        email_smtp_password="secret",
    )
    assert s.email_backend == "smtp"


# ---------------------------------------------------------------------------
# Invariant validators
# ---------------------------------------------------------------------------


def test_lease_must_exceed_timeout_raises_when_equal():
    """email_claim_lease_seconds == email_timeout_seconds is invalid (must be strictly >)."""
    with pytest.raises(ValidationError, match="email_claim_lease_seconds"):
        make(
            email_claim_lease_seconds=15,
            email_timeout_seconds=15.0,
        )


def test_lease_must_exceed_timeout_raises_when_less():
    """email_claim_lease_seconds < email_timeout_seconds is invalid."""
    with pytest.raises(ValidationError, match="email_claim_lease_seconds"):
        make(
            email_claim_lease_seconds=10,
            email_timeout_seconds=30.0,
        )


def test_lease_exceeds_timeout_is_valid():
    s = make(email_claim_lease_seconds=60, email_timeout_seconds=15.0)
    assert s.email_claim_lease_seconds == 60


def test_backoff_max_less_than_backoff_raises():
    """email_retry_backoff_max_seconds < email_retry_backoff_seconds is invalid."""
    with pytest.raises(ValidationError, match="email_retry_backoff_max_seconds"):
        make(
            email_retry_backoff_seconds=30.0,
            email_retry_backoff_max_seconds=10.0,
        )


def test_backoff_max_equals_backoff_is_valid():
    """email_retry_backoff_max_seconds == email_retry_backoff_seconds is allowed."""
    s = make(email_retry_backoff_seconds=10.0, email_retry_backoff_max_seconds=10.0)
    assert s.email_retry_backoff_max_seconds == 10.0


def test_negative_max_retries_raises():
    with pytest.raises(ValidationError, match="email_max_retries"):
        make(email_max_retries=-1)


def test_zero_max_retries_is_valid():
    s = make(email_max_retries=0)
    assert s.email_max_retries == 0


def test_invalid_from_address_plain_string_raises():
    """A plain non-email string is rejected."""
    with pytest.raises(ValidationError, match="email_from_address"):
        make(email_from_address="not-an-email")


def test_invalid_from_address_missing_domain_raises():
    """An address missing the domain part is rejected."""
    with pytest.raises(ValidationError, match="email_from_address"):
        make(email_from_address="user@")


def test_valid_from_address_bare():
    s = make(email_from_address="sender@example.com")
    assert s.email_from_address == "sender@example.com"


def test_valid_from_address_display_name():
    s = make(email_from_address="SF Bulk Loader <noreply@example.com>")
    assert s.email_from_address == "SF Bulk Loader <noreply@example.com>"


def test_none_from_address_is_valid():
    """email_from_address=None is allowed (not all deployments need it set)."""
    s = make(email_from_address=None)
    assert s.email_from_address is None
