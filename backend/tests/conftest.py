"""Shared pytest configuration for the backend test suite.

API tests use a synchronous TestClient backed by a file-based SQLite DB so that
FastAPI's internal anyio event loop and pytest-asyncio's loop don't conflict.
A fresh Fernet key is generated at session start and injected via os.environ
before any app code is imported.
"""

import asyncio
import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── Set test environment BEFORE importing any app modules ─────────────────────
_TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()
os.environ.setdefault("ENCRYPTION_KEY", _TEST_ENCRYPTION_KEY)

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
import app.services.orchestrator as _orchestrator_module  # noqa: E402

# Override in-process settings so encrypt/decrypt helpers use our key
settings.encryption_key = _TEST_ENCRYPTION_KEY

# ── Test database ─────────────────────────────────────────────────────────────

TEST_DB_PATH = "./test_api.db"
TEST_DB_URL = f"sqlite+aiosqlite:///{TEST_DB_PATH}"

_engine = create_async_engine(TEST_DB_URL, echo=False)
_TestSession = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


def _run_async(coro):
    """Execute a coroutine in a temporary event loop (safe outside any running loop)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Create all tables once per session; drop them and remove the file on teardown."""

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
    for path in (TEST_DB_PATH, TEST_DB_PATH + "-shm", TEST_DB_PATH + "-wal"):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


@pytest.fixture(autouse=True)
def clean_db():
    """Delete all rows between tests for isolation."""
    yield
    from sqlalchemy import delete

    from app.models.connection import Connection
    from app.models.job import JobRecord
    from app.models.load_plan import LoadPlan
    from app.models.load_run import LoadRun
    from app.models.load_step import LoadStep

    async def _clean():
        async with _TestSession() as session:
            for model in [JobRecord, LoadRun, LoadStep, LoadPlan, Connection]:
                await session.execute(delete(model))
            await session.commit()

    _run_async(_clean())


@pytest.fixture
def client():
    """Return a synchronous TestClient with the test DB wired in.

    Patches the orchestrator so that background tasks triggered by the
    ``/run`` endpoint are no-ops — the run record is created in the DB but
    the orchestrator does not execute.  This prevents a race where the
    orchestrator fails immediately (fake key) and changes run status before
    the test can act.  Orchestrator behaviour is covered by test_orchestrator.py
    which calls _execute_run directly.
    """

    async def override_get_db():
        async with _TestSession() as session:
            yield session

    async def _noop_execute_run(_run_id: str) -> None:  # noqa: RUF029
        pass

    app.dependency_overrides[get_db] = override_get_db
    _original_execute_run = _orchestrator_module.execute_run
    _orchestrator_module.execute_run = _noop_execute_run
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    _orchestrator_module.execute_run = _original_execute_run
    app.dependency_overrides.clear()
