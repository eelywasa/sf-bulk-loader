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


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """Return a fresh async session for the email test DB."""
    async with EmailTestSession() as s:
        yield s
