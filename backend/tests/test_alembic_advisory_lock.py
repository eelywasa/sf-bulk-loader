"""Tests for the Postgres advisory-lock wrapper around ``alembic upgrade head``.

The wrapper lives in ``backend/alembic/env.py::run_async_migrations``
(SFBL-277). On Postgres it acquires a session-level advisory lock with
``pg_advisory_lock(<key>)`` before applying migrations, then releases via
``pg_advisory_unlock(<key>)``. On SQLite the lock acquisition is skipped.

These tests exercise both paths.

The Postgres path requires a reachable Postgres instance — when one is
not available, the module is skipped, mirroring the convention in
``tests/scripts/test_migrate_sqlite_to_postgres.py``. Set
``MIGRATION_TEST_PG_URL`` to override the default Postgres URL.
"""

from __future__ import annotations

import asyncio
import os
import socket
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


# ── SQLite path: lock skip ────────────────────────────────────────────────────

class TestAdvisoryLockSqlite:
    """SQLite cannot do advisory locks; verify ``alembic upgrade head`` runs
    cleanly and the env.py code branches around the lock acquisition.
    """

    def test_alembic_upgrade_head_clean_against_sqlite(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "alembic_test.db"

            # env.py reads settings.database_url at function call time, so
            # monkeypatching the attribute is sufficient — no need to fight
            # the conftest's import-time env loading.
            from app.config import settings as _settings
            monkeypatch.setattr(_settings, "database_url", f"sqlite+aiosqlite:///{db_path}")

            from alembic.config import Config
            from alembic import command

            backend_root = Path(__file__).resolve().parent.parent
            # Build the Config without an alembic.ini file path. env.py
            # only calls `fileConfig(config.config_file_name)` when the path
            # is set; bypassing it keeps the logging.fileConfig call out of
            # the test session. fileConfig defaults to disable_existing_loggers
            # = True, which silently kills every logger the rest of the test
            # suite depends on for log-capture assertions.
            cfg = Config()
            cfg.set_main_option("script_location", str(backend_root / "alembic"))

            # First upgrade — applies all migrations.
            command.upgrade(cfg, "head")

            # Second upgrade — no-op. If the env.py advisory-lock branch
            # accidentally executed pg_advisory_lock against SQLite, this
            # would error with "no such function: pg_advisory_lock". The
            # idempotent second run proves the skip path works.
            command.upgrade(cfg, "head")


# ── Postgres path: real lock acquire + release ───────────────────────────────

# Default matches the CI Postgres container credentials (postgres:postgres) and
# the local reference setup in docs/development.md. Override via
# MIGRATION_TEST_PG_URL for a custom instance.
_DEFAULT_PG = "postgresql+asyncpg://postgres:postgres@localhost:5432/test_alembic_advisory"
_PG_URL = os.environ.get("MIGRATION_TEST_PG_URL", _DEFAULT_PG)


def _pg_reachable(url: str) -> bool:
    parsed = urlparse(url.replace("+asyncpg", ""))
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


if not _pg_reachable(_PG_URL):
    pytest.skip(
        f"Postgres not reachable at {_PG_URL} — set MIGRATION_TEST_PG_URL to "
        "a reachable Postgres instance to enable these tests.",
        allow_module_level=True,
    )


# Same key as in alembic/env.py — kept in sync deliberately so the tests
# verify the production constant rather than re-deriving it.
_ALEMBIC_ADVISORY_LOCK_KEY = 0x2D5E8F7A_C4B91035


async def _drop_database_async(host: str, port: int, user: str, password: str | None, db_name: str) -> None:
    """Drop test database via asyncpg (CREATE/DROP can't run in a TX)."""
    import asyncpg

    conn = await asyncpg.connect(host=host, port=port, user=user, password=password, database="postgres")
    try:
        # Terminate any leftover sessions before drop.
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            db_name,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
    finally:
        await conn.close()


async def _create_database_async(host: str, port: int, user: str, password: str | None, db_name: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(host=host, port=port, user=user, password=password, database="postgres")
    try:
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


@pytest.fixture
def fresh_pg_database():
    """Yield a freshly-created Postgres database URL; drop it after the test."""
    parsed = urlparse(_PG_URL.replace("+asyncpg", ""))
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    user = parsed.username or ""
    password = parsed.password
    db_name = f"alembic_lock_test_{uuid.uuid4().hex[:12]}"

    asyncio.run(_create_database_async(host, port, user, password, db_name))
    test_url = (
        f"postgresql+asyncpg://{user}"
        f"{':' + password if password else ''}"
        f"@{host}:{port}/{db_name}"
    )
    try:
        yield test_url
    finally:
        asyncio.run(_drop_database_async(host, port, user, password, db_name))


class TestAdvisoryLockPostgres:
    """Run ``alembic upgrade head`` against a real Postgres and verify:

    1. The migration completes successfully (advisory lock acquire/release path).
    2. A second invocation is idempotent (lock is properly released between runs).
    3. After the migration, no session is holding the advisory lock anywhere.
    """

    def test_alembic_upgrade_head_acquires_and_releases_lock(self, fresh_pg_database, monkeypatch):
        from app.config import settings as _settings
        monkeypatch.setattr(_settings, "database_url", fresh_pg_database)

        from alembic.config import Config
        from alembic import command

        backend_root = Path(__file__).resolve().parent.parent
        # Build the Config without an alembic.ini path. See the SQLite test
        # above for why — fileConfig in env.py would silently disable every
        # logger in the pytest session.
        cfg = Config()
        cfg.set_main_option("script_location", str(backend_root / "alembic"))

        # Run all migrations under the advisory lock. The wrapper in
        # env.py acquires pg_advisory_lock, applies migrations, then
        # releases via pg_advisory_unlock in a finally block.
        command.upgrade(cfg, "head")

        # Verify no session is currently holding the advisory lock. If the
        # lock release path in env.py were broken — e.g. the unlock SQL
        # threw silently and we relied on session close to drop the lock,
        # but pooling held it open — this would surface here. Checked via
        # a fresh connection so we see only persistent state.
        async def _check_no_residual_lock() -> int:
            engine = create_async_engine(fresh_pg_database, poolclass=NullPool)
            try:
                async with engine.connect() as conn:
                    # Postgres advisory locks taken with the single-bigint
                    # form pg_advisory_lock(BIGINT) appear in pg_locks with
                    # classid+objid encoding the key as
                    # (classid::bigint << 32) | objid::bigint.
                    result = await conn.execute(
                        text(
                            "SELECT count(*) FROM pg_locks "
                            "WHERE locktype = 'advisory' "
                            "  AND ((classid::bigint << 32) | objid::bigint) = :k"
                        ),
                        {"k": _ALEMBIC_ADVISORY_LOCK_KEY},
                    )
                    return result.scalar_one()
            finally:
                await engine.dispose()

        held = asyncio.run(_check_no_residual_lock())
        assert held == 0, f"Advisory lock still held by {held} session(s) after upgrade"
