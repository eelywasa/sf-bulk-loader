"""Shared fixtures for email service tests.

Uses a file-based SQLite database (separate from the main API test DB) so
email delivery log tests are fully isolated and don't require any tables
from the production conftest.

We use a file-based DB (not :memory:) to avoid StaticPool issues and
session-scoped event loop conflicts. Each test session gets a fresh DB file.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Ensure env vars are set before importing any app modules
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("JWT_SECRET_KEY", "email-test-jwt-secret")
os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "Test-Admin-P4ss!")

from app.database import Base  # noqa: E402
from app.models.email_delivery import EmailDelivery  # noqa: E402, F401 — registers model

# ---------------------------------------------------------------------------
# Default email settings values — used by the autouse settings_service_mock
# below.  Tests that need different values can monkeypatch or override
# _EMAIL_DEFAULTS directly.
# ---------------------------------------------------------------------------
_EMAIL_DEFAULTS: dict[str, object] = {
    "email_backend": "noop",
    "email_from_address": "",
    "email_from_name": "",
    "email_reply_to": "",
    "email_max_retries": 3,
    "email_retry_backoff_seconds": 2.0,
    "email_retry_backoff_max_seconds": 120.0,
    "email_timeout_seconds": 15.0,
    "email_claim_lease_seconds": 60,
    "email_pending_stale_minutes": 15,
    "email_log_recipients": False,
    "email_smtp_host": "",
    "email_smtp_port": 587,
    "email_smtp_username": "",
    "email_smtp_password": "",
    "email_smtp_starttls": True,
    "email_smtp_use_tls": False,
    "email_ses_region": "",
    "email_ses_configuration_set": "",
    "frontend_base_url": "",
}


# File-based SQLite for email tests; NullPool so each session gets its own
# connection — avoids the StaticPool shared-connection issue with asyncio.
_EMAIL_DB_FILE = os.path.join(tempfile.gettempdir(), "sfbl_email_test.db")
_EMAIL_TEST_DB_URL = f"sqlite+aiosqlite:///{_EMAIL_DB_FILE}"

_engine = create_async_engine(
    _EMAIL_TEST_DB_URL,
    echo=False,
    poolclass=NullPool,
)

EmailTestSession = async_sessionmaker(
    _engine, class_=AsyncSession, expire_on_commit=False
)


def _run_async(coro):
    """Run a coroutine synchronously in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(scope="session", autouse=True)
def create_email_tables():
    """Create all tables once per test session."""
    async def _create():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _drop():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await _engine.dispose()

    _run_async(_create())
    yield
    _run_async(_drop())
    # Remove the temp DB file
    for path in (_EMAIL_DB_FILE, _EMAIL_DB_FILE + "-shm", _EMAIL_DB_FILE + "-wal"):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


@pytest_asyncio.fixture(autouse=True)
async def clean_email_delivery():
    """Truncate email_delivery between tests."""
    yield
    from sqlalchemy import delete

    async with EmailTestSession() as session:
        await session.execute(delete(EmailDelivery))
        await session.commit()


@pytest.fixture(autouse=True)
def email_settings_service_mock():
    """Install a default SettingsService mock for all email tests.

    All calls to ``settings_service.get(key)`` return values from
    ``_EMAIL_DEFAULTS``.  Individual tests that need different values should
    patch ``app.services.settings.service.settings_service`` directly or
    update ``_EMAIL_DEFAULTS`` in a narrowly scoped fixture.
    """
    import app.services.settings.service as _svc_module

    class _DefaultSvc:
        async def get(self, key: str) -> object:
            return _EMAIL_DEFAULTS.get(key)

    original = _svc_module.settings_service
    _svc_module.settings_service = _DefaultSvc()  # type: ignore[assignment]
    yield
    _svc_module.settings_service = original


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """Return a fresh async session for the email test DB."""
    async with EmailTestSession() as s:
        yield s
