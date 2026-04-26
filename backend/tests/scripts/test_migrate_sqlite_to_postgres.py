"""Tests for scripts/migrate_sqlite_to_postgres.py (SFBL-272).

Drives the internal async entry points (_run_validate, _run_migrate, _run_verify)
directly rather than invoking the CLI via subprocess, following the same pattern
as tests/test_cli.py.

Requires a reachable Postgres instance.  Set MIGRATION_TEST_PG_URL to override
the default:
    MIGRATION_TEST_PG_URL=postgresql+asyncpg://user:pass@host:5432/mydb pytest \\
        tests/scripts/test_migrate_sqlite_to_postgres.py

The default assumes Postgres.app running locally with the developer's OS user.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

# ── Import the migration script ───────────────────────────────────────────────
# The script lives two levels up from this file's project root, in scripts/.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

import migrate_sqlite_to_postgres as mig  # noqa: E402

# ── App models (conftest has already imported them into Base.metadata) ────────
from app.database import Base  # noqa: E402
from app.models.app_setting import AppSetting  # noqa: E402
from app.models.connection import Connection  # noqa: E402
from app.models.input_connection import InputConnection  # noqa: E402
from app.models.job import JobRecord  # noqa: E402
from app.models.load_plan import LoadPlan  # noqa: E402
from app.models.load_run import LoadRun  # noqa: E402
from app.models.load_step import LoadStep  # noqa: E402
from app.models.notification_subscription import (  # noqa: E402
    NotificationChannel,
    NotificationSubscription,
    NotificationTrigger,
)
from app.models.profile import Profile  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_totp import UserTotp  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────
_DEFAULT_PG = "postgresql+asyncpg://mjenkin@localhost:5432/test_migration"
_PG_URL = os.environ.get("MIGRATION_TEST_PG_URL", _DEFAULT_PG)
_ALEMBIC_REV = "0028"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def encryption_key() -> str:
    """Return the encryption key in use for this test session."""
    return os.environ["ENCRYPTION_KEY"]


@pytest.fixture(scope="module")
def fernet(encryption_key: str) -> Fernet:
    return Fernet(encryption_key.encode())


@pytest.fixture()
async def sqlite_db(tmp_path, fernet):
    """Create a seeded SQLite database; yield its file path string."""
    db_path = tmp_path / "source.db"
    src_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(src_url, poolclass=NullPool)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        await conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
            {"v": _ALEMBIC_REV},
        )

    async with AsyncSession(engine) as session:
        # Connection with encrypted private_key
        conn_id = str(uuid.uuid4())
        plan_id = str(uuid.uuid4())
        step1_id = str(uuid.uuid4())
        step2_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        ic_id = str(uuid.uuid4())
        profile_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        totp_id = str(uuid.uuid4())

        conn_obj = Connection(
            id=conn_id,
            name="Test Org",
            instance_url="https://test.salesforce.com",
            login_url="https://login.salesforce.com",
            client_id="client123",
            private_key=fernet.encrypt(b"-----BEGIN RSA PRIVATE KEY-----").decode(),
            username="test@example.com",
            is_sandbox=False,
        )
        plan = LoadPlan(
            id=plan_id,
            name="Test Plan",
            connection_id=conn_id,
            max_parallel_jobs=3,
        )
        step1 = LoadStep(
            id=step1_id,
            load_plan_id=plan_id,
            object_name="Account",
            operation="insert",
            csv_file_pattern="/data/input/accounts*.csv",
            sequence=1,
            name="accounts_step",
        )
        step2 = LoadStep(
            id=step2_id,
            load_plan_id=plan_id,
            object_name="Contact",
            operation="insert",
            csv_file_pattern="/data/input/contacts*.csv",
            sequence=2,
            input_from_step_id=step1_id,
        )
        run = LoadRun(
            id=run_id,
            load_plan_id=plan_id,
            status="completed",
        )
        job = JobRecord(
            id=job_id,
            load_run_id=run_id,
            load_step_id=step1_id,
            partition_index=0,
            status="job_complete",
            records_processed=100,
            records_failed=0,
        )
        ic = InputConnection(
            id=ic_id,
            name="S3 Source",
            provider="s3",
            bucket="my-bucket",
            access_key_id=fernet.encrypt(b"AKIAIOSFODNN7EXAMPLE").decode(),
            secret_access_key=fernet.encrypt(b"wJalrXUtnFEMI/K7MDENG").decode(),
        )
        profile = Profile(
            id=profile_id,
            name="Operator",
            is_system=False,
        )
        user = User(
            id=user_id,
            email="operator@example.com",
            display_name="Operator",
            hashed_password="$2b$12$fakehashfakehashfakehashhashXX",
            status="active",
        )
        totp = UserTotp(
            id=totp_id,
            user_id=user_id,
            secret_encrypted=fernet.encrypt(b"JBSWY3DPEHPK3PXP").decode(),
        )
        setting = AppSetting(
            key="smtp_password",
            value=fernet.encrypt(b"s3cret").decode(),
            is_encrypted=True,
            category="email",
        )

        session.add_all(
            [conn_obj, plan, step1, step2, run, job, ic, profile, user, totp, setting]
        )
        await session.commit()

    await engine.dispose()
    yield str(db_path)


async def _pg_reset(engine) -> None:
    """Nuke and recreate the public schema — most reliable full teardown."""
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        await conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
            {"v": _ALEMBIC_REV},
        )


@pytest.fixture()
async def postgres_db():
    """Create a clean Postgres schema; restore cleanliness after each test."""
    engine = create_async_engine(_PG_URL, poolclass=NullPool)
    await _pg_reset(engine)
    yield _PG_URL
    await _pg_reset(engine)
    await engine.dispose()


# ── validate tests ────────────────────────────────────────────────────────────

class TestValidate:
    async def test_green_path(self, sqlite_db, postgres_db):
        rc = await mig._run_validate(sqlite_db, postgres_db, force=False, backup_confirmed=False)
        assert rc == 0

    async def test_missing_source(self, postgres_db, tmp_path):
        rc = await mig._run_validate(
            str(tmp_path / "nonexistent.db"), postgres_db, force=False, backup_confirmed=False
        )
        assert rc > 0

    async def test_alembic_mismatch(self, sqlite_db, tmp_path):
        wrong_rev_db = tmp_path / "wrong.db"
        wrong_url = f"sqlite+aiosqlite:///{wrong_rev_db}"
        engine = create_async_engine(wrong_url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version "
                    "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            await conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": "0001"},
            )
        await engine.dispose()

        pg_engine = create_async_engine(_PG_URL, poolclass=NullPool)
        await _pg_reset(pg_engine)
        await pg_engine.dispose()

        rc = await mig._run_validate(
            str(wrong_rev_db), _PG_URL, force=False, backup_confirmed=False
        )
        assert rc > 0

    async def test_non_empty_target_blocked_without_force(self, sqlite_db, postgres_db):
        pg_engine = create_async_engine(postgres_db, poolclass=NullPool)
        async with pg_engine.begin() as conn:
            # Inject a row into a table to simulate non-empty target
            await conn.execute(
                text(
                    "INSERT INTO app_settings (key, value, is_encrypted, category, updated_at) "
                    "VALUES ('test', 'val', false, 'test', NOW())"
                )
            )
        await pg_engine.dispose()

        rc = await mig._run_validate(sqlite_db, postgres_db, force=False, backup_confirmed=False)
        assert rc > 0

    async def test_non_empty_target_allowed_with_force_and_backup(self, sqlite_db, postgres_db):
        pg_engine = create_async_engine(postgres_db, poolclass=NullPool)
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO app_settings (key, value, is_encrypted, category, updated_at) "
                    "VALUES ('test2', 'val', false, 'test', NOW())"
                )
            )
        await pg_engine.dispose()

        rc = await mig._run_validate(sqlite_db, postgres_db, force=True, backup_confirmed=True)
        assert rc == 0


# ── migrate tests ─────────────────────────────────────────────────────────────

class TestMigrate:
    async def test_happy_path_row_counts(self, sqlite_db, postgres_db, fernet):
        rc = await mig._run_migrate(
            sqlite_db, postgres_db, force=False, backup_confirmed=False, batch_size=500
        )
        assert rc == 0

        pg_engine = create_async_engine(postgres_db, poolclass=NullPool)
        async with pg_engine.connect() as conn:
            # Core tables should have rows
            assert (await conn.execute(text("SELECT COUNT(*) FROM connection"))).scalar_one() == 1
            assert (await conn.execute(text("SELECT COUNT(*) FROM load_plan"))).scalar_one() == 1
            assert (await conn.execute(text("SELECT COUNT(*) FROM load_step"))).scalar_one() == 2
            assert (await conn.execute(text("SELECT COUNT(*) FROM load_run"))).scalar_one() == 1
            assert (await conn.execute(text("SELECT COUNT(*) FROM job_record"))).scalar_one() == 1
            assert (await conn.execute(text("SELECT COUNT(*) FROM input_connection"))).scalar_one() == 1
            assert (await conn.execute(text("SELECT COUNT(*) FROM user_totp"))).scalar_one() == 1
            assert (await conn.execute(text("SELECT COUNT(*) FROM app_settings"))).scalar_one() == 1
        await pg_engine.dispose()

    async def test_encrypted_columns_decrypt_on_target(self, sqlite_db, postgres_db, fernet):
        await mig._run_migrate(
            sqlite_db, postgres_db, force=False, backup_confirmed=False, batch_size=500
        )

        pg_engine = create_async_engine(postgres_db, poolclass=NullPool)
        async with pg_engine.connect() as conn:
            pk = (await conn.execute(text("SELECT private_key FROM connection LIMIT 1"))).scalar_one()
            assert fernet.decrypt(pk.encode()) == b"-----BEGIN RSA PRIVATE KEY-----"

            ak = (await conn.execute(text("SELECT access_key_id FROM input_connection LIMIT 1"))).scalar_one()
            assert fernet.decrypt(ak.encode()) == b"AKIAIOSFODNN7EXAMPLE"

            secret = (await conn.execute(text("SELECT secret_encrypted FROM user_totp LIMIT 1"))).scalar_one()
            assert fernet.decrypt(secret.encode()) == b"JBSWY3DPEHPK3PXP"

            setting_val = (
                await conn.execute(
                    text("SELECT value FROM app_settings WHERE key='smtp_password'")
                )
            ).scalar_one()
            assert fernet.decrypt(setting_val.encode()) == b"s3cret"
        await pg_engine.dispose()

    async def test_input_from_step_id_preserved(self, sqlite_db, postgres_db):
        await mig._run_migrate(
            sqlite_db, postgres_db, force=False, backup_confirmed=False, batch_size=500
        )

        pg_engine = create_async_engine(postgres_db, poolclass=NullPool)
        async with pg_engine.connect() as conn:
            result = (
                await conn.execute(
                    text(
                        "SELECT id, input_from_step_id, name FROM load_step ORDER BY sequence"
                    )
                )
            ).all()
            step1, step2 = result
            assert step1[1] is None
            assert step1[2] == "accounts_step"
            assert step2[1] == step1[0]  # FK references step1
        await pg_engine.dispose()

    async def test_rollback_on_constraint_violation(self, tmp_path, postgres_db, fernet):
        """A row that violates a Postgres VARCHAR length must roll back the whole migration.

        SQLite ignores column length limits (TEXT is unbounded). Postgres VARCHAR(512)
        enforces the limit, so a >512-char instance_url accepted by SQLite will cause
        the Postgres INSERT to fail.  We assert that the entire migration rolls back.
        """
        db_path = tmp_path / "bad_source.db"
        src_url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(src_url, poolclass=NullPool)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version "
                    "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            await conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": _ALEMBIC_REV},
            )
            # instance_url is VARCHAR(512) in Postgres — SQLite ignores the limit.
            oversized_url = "https://too-long.salesforce.com/" + ("x" * 512)
            await conn.execute(
                text(
                    "INSERT INTO connection "
                    "(id, name, instance_url, login_url, client_id, private_key, username, is_sandbox, created_at, updated_at) "
                    "VALUES (:id, 'Bad', :url, 'https://login.sf.com', 'cli', :pk, 'u@x.com', 0, datetime('now'), datetime('now'))"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "url": oversized_url,
                    "pk": fernet.encrypt(b"key").decode(),
                },
            )
        await engine.dispose()

        rc = await mig._run_migrate(
            str(db_path), postgres_db, force=False, backup_confirmed=False, batch_size=500
        )
        assert rc == 1

        # Postgres must be empty (full rollback)
        pg_engine = create_async_engine(postgres_db, poolclass=NullPool)
        async with pg_engine.connect() as conn:
            cnt = (await conn.execute(text("SELECT COUNT(*) FROM connection"))).scalar_one()
            assert cnt == 0
        await pg_engine.dispose()


# ── verify tests ──────────────────────────────────────────────────────────────

class TestVerify:
    async def test_green_after_migrate(self, sqlite_db, postgres_db):
        await mig._run_migrate(
            sqlite_db, postgres_db, force=False, backup_confirmed=False, batch_size=500
        )
        rc = await mig._run_verify(sqlite_db, postgres_db)
        assert rc == 0

    async def test_fails_when_row_missing_on_target(self, sqlite_db, postgres_db):
        await mig._run_migrate(
            sqlite_db, postgres_db, force=False, backup_confirmed=False, batch_size=500
        )
        # Delete a row on the target to simulate corruption
        pg_engine = create_async_engine(postgres_db, poolclass=NullPool)
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM load_step WHERE sequence = 2"))
        await pg_engine.dispose()

        rc = await mig._run_verify(sqlite_db, postgres_db)
        assert rc > 0
