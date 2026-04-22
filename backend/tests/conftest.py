"""Shared pytest configuration for the backend test suite.

API tests use a synchronous TestClient backed by a file-based SQLite DB so that
FastAPI's internal anyio event loop and pytest-asyncio's loop don't conflict.
A fresh Fernet key is generated at session start and injected via os.environ
before any app code is imported.

To run the suite against PostgreSQL, set TEST_DATABASE_URL before invoking pytest:
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/test_db pytest
"""

import asyncio
import os
import sqlite3

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# ── Isolate tests from the repo-root `.env` file ─────────────────────────────
# `app.config.Settings` is configured with `env_file=("../.env", ".env")`. In
# a developer checkout the parent `.env` is the repo-root file used for
# `docker compose up`, which sets EMAIL_BACKEND=smtp, APP_DISTRIBUTION,
# LOG_LEVEL, etc. Those leak into every `Settings()` construction during the
# test run — flipping profile-default assertions in test_config_email and
# pushing the dependency health check into "degraded".
#
# Fix: set SFBL_DISABLE_ENV_FILE=1 BEFORE importing any app module. The
# config module's `Settings.model_config` reads this flag at import time and
# sets `env_file=()`, skipping `.env` loading for the whole test session.
# Also scrub the same keys from os.environ in case the developer exported
# them in their shell — tests should see pristine defaults unless the test
# sets its own value.
#
# CI is unaffected (no `.env` present), but this keeps local and CI runs
# identical.
os.environ["SFBL_DISABLE_ENV_FILE"] = "1"

for _pollutant in (
    "EMAIL_BACKEND",
    "EMAIL_FROM_ADDRESS",
    "EMAIL_SMTP_HOST",
    "EMAIL_SMTP_PORT",
    "EMAIL_SMTP_STARTTLS",
    "EMAIL_SMTP_USERNAME",
    "EMAIL_SMTP_PASSWORD",
    "APP_DISTRIBUTION",
    "APP_ENV",
    "LOG_LEVEL",
    "CORS_ORIGINS",
):
    os.environ.pop(_pollutant, None)

# ── Set test environment BEFORE importing any app modules ─────────────────────
_TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()
os.environ.setdefault("ENCRYPTION_KEY", _TEST_ENCRYPTION_KEY)
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-for-pytest-only")
os.environ.setdefault("ADMIN_EMAIL", "test-admin@example.com")
os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "Test-Admin-P4ss!")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
import app.database as _db_module  # noqa: E402
import app.models  # noqa: E402,F401  # register all ORM models with Base.metadata before create_all
import app.main as _main_module  # noqa: E402
from app.main import app  # noqa: E402
import app.services.orchestrator as _orchestrator_module  # noqa: E402

# Override in-process settings so encrypt/decrypt helpers use our key
settings.encryption_key = _TEST_ENCRYPTION_KEY
settings.admin_email = "test-admin@example.com"
settings.admin_username = "test-admin"
settings.admin_password = "Test-Admin-P4ss!"

# ── Test database ─────────────────────────────────────────────────────────────

_DEFAULT_TEST_DB_PATH = "./test_api.db"
_DEFAULT_TEST_DB_URL = f"sqlite+aiosqlite:///{_DEFAULT_TEST_DB_PATH}"
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL") or _DEFAULT_TEST_DB_URL
_is_sqlite = TEST_DB_URL.startswith("sqlite")

# NullPool disables connection pooling so each checkout gets a fresh connection
# on the current event loop. This is required when _run_async() creates a new
# event loop per call — asyncpg connections are loop-bound and cannot be reused
# across loops, which causes "another operation is in progress" on teardown.
_engine = create_async_engine(TEST_DB_URL, echo=False, poolclass=NullPool)
_TestSession = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

if _is_sqlite:
    @event.listens_for(_engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# Point the session factories used by lifespan seed and app routes to the test DB
_db_module.AsyncSessionLocal = _TestSession
_main_module.AsyncSessionLocal = _TestSession


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
        # Seed profile rows that migration 0021 would normally create —
        # Base.metadata.create_all only creates tables, not seed data.
        from app.auth.permissions import ALL_PERMISSION_KEYS
        from app.models.profile import Profile
        from app.models.profile_permission import ProfilePermission

        _PROFILE_PERMISSIONS = {
            "admin": sorted(ALL_PERMISSION_KEYS),
            "operator": [
                "connections.view", "connections.manage",
                "plans.view", "plans.manage",
                "runs.view", "runs.execute", "runs.abort",
                "files.view", "files.view_contents",
            ],
            "viewer": [
                "connections.view",
                "plans.view",
                "runs.view",
                "files.view",
            ],
        }
        async with _TestSession() as session:
            for name, keys in _PROFILE_PERMISSIONS.items():
                profile = Profile(name=name, description=f"{name} profile (test seed)", is_system=True)
                session.add(profile)
                await session.flush()
                for key in keys:
                    session.add(ProfilePermission(profile_id=profile.id, permission_key=key))
            await session.commit()

    async def _drop():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await _engine.dispose()

    _run_async(_create())
    yield
    _run_async(_drop())
    if _is_sqlite:
        for path in (_DEFAULT_TEST_DB_PATH, _DEFAULT_TEST_DB_PATH + "-shm", _DEFAULT_TEST_DB_PATH + "-wal"):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


@pytest.fixture(autouse=True)
def clean_db():
    """Delete all rows between tests for isolation."""
    yield
    from sqlalchemy import delete

    from app.models.app_setting import AppSetting
    from app.models.connection import Connection
    from app.models.input_connection import InputConnection
    from app.models.invitation_token import InvitationToken
    from app.models.job import JobRecord
    from app.models.load_plan import LoadPlan
    from app.models.load_run import LoadRun
    from app.models.load_step import LoadStep
    from app.models.login_attempt import LoginAttempt
    from app.models.notification_delivery import NotificationDelivery
    from app.models.notification_subscription import NotificationSubscription
    from app.models.user import User

    async def _clean():
        async with _TestSession() as session:
            for model in [
                AppSetting,
                LoginAttempt,
                NotificationDelivery,
                NotificationSubscription,
                JobRecord,
                LoadRun,
                LoadStep,
                LoadPlan,
                Connection,
                InputConnection,
                InvitationToken,  # FK → user; must come before User
                User,
            ]:
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


@pytest.fixture
def auth_client():
    """Authenticated TestClient with get_current_user overridden.

    Use this fixture for tests that exercise protected endpoints.  The
    dependency override injects a synthetic active user so tests do not need
    to seed and log in a real account.

    The mock user is given the seeded admin Profile so that require_permission()
    grants all permissions — the is_admin=True backstop was removed in SFBL-203.
    """
    import uuid

    from sqlalchemy import select as sa_select

    from app.models.profile import Profile
    from app.models.profile_permission import ProfilePermission
    from app.models.user import User
    from app.services.auth import get_current_user

    # Fetch the admin profile that was seeded by setup_test_database.
    async def _get_admin_profile():
        async with _TestSession() as session:
            result = await session.execute(
                sa_select(Profile).where(Profile.name == "admin")
            )
            profile = result.scalar_one_or_none()
            if profile is None:
                return None, []
            # Eagerly load permission keys so they are accessible outside the session.
            perm_result = await session.execute(
                sa_select(ProfilePermission).where(
                    ProfilePermission.profile_id == profile.id
                )
            )
            perms = perm_result.scalars().all()
            return profile.id, [p.permission_key for p in perms]

    admin_profile_id, admin_perm_keys = _run_async(_get_admin_profile())

    # Build a detached Profile object with permission_keys cached so the user
    # passes require_permission() checks without a DB round-trip.
    from app.auth.permissions import ALL_PERMISSION_KEYS

    _admin_profile = Profile(
        id=admin_profile_id or str(uuid.uuid4()),
        name="admin",
        description="admin profile (test)",
        is_system=True,
    )
    _admin_profile.permissions = [
        ProfilePermission(
            profile_id=_admin_profile.id,
            permission_key=k,
        )
        for k in (admin_perm_keys or sorted(ALL_PERMISSION_KEYS))
    ]

    _mock_user = User(
        id=str(uuid.uuid4()),
        email="test-user@example.com",
        hashed_password="x",
        is_admin=True,
        status="active",
        profile_id=_admin_profile.id,
    )
    _mock_user.profile = _admin_profile

    async def override_get_db():
        async with _TestSession() as session:
            yield session

    async def override_get_current_user():
        return _mock_user

    async def _noop_execute_run(_run_id: str) -> None:  # noqa: RUF029
        pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    _original_execute_run = _orchestrator_module.execute_run
    _orchestrator_module.execute_run = _noop_execute_run
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    _orchestrator_module.execute_run = _original_execute_run
    app.dependency_overrides.clear()
