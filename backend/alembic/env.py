import asyncio
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import Base so all models are registered on its metadata
from app.database import Base
from app.models import Connection, EmailDelivery, InputConnection, JobRecord, LoadPlan, LoadRun, LoadStep, LoginAttempt, Profile, ProfilePermission  # noqa: F401

_log = logging.getLogger("alembic.env")

# Postgres advisory-lock key for the alembic upgrade serialisation lock.
# Random 64-bit constant chosen specifically for this project — collisions
# with other applications using advisory locks on the same DB would be
# unfortunate but the bit-space is large enough that they're not a concern.
# Kept here (not in app config) because alembic env.py runs before app
# config is loaded.
_ALEMBIC_ADVISORY_LOCK_KEY = 0x2D5E8F7A_C4B91035  # noqa: E501

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """Return the database URL from app config (overrides alembic.ini)."""
    from app.config import settings
    return settings.database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout, no live DB)."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),  # SQLite requires batch for ALTER TABLE
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    use_batch = connection.engine.dialect.name == "sqlite"
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=use_batch,  # SQLite requires batch for ALTER TABLE
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against a live async engine.

    On Postgres, hold a session-level advisory lock for the duration of
    the upgrade. This serialises concurrent ``alembic upgrade head`` runs
    even when they come from different processes — the second caller
    blocks on ``pg_advisory_lock`` until the first releases it, then
    typically observes the schema is already at head and exits cleanly
    in O(milliseconds).

    The advisory lock is belt-and-braces: the canonical aws_hosted deploy
    path runs migrations from a single one-shot ECS task before service
    rollout (RUN_MIGRATIONS=false on the service tasks, see Dockerfile
    + SFBL-277). The lock catches the case where someone bypasses that
    flow — either via a misconfigured env var or by running
    ``alembic upgrade head`` from a bastion host while a deploy is
    already in flight.

    SQLite has no advisory locks; the lock acquisition is skipped on
    that backend. Self-hosted Docker compose typically runs SQLite with
    a single container, so concurrency isn't a concern there anyway.
    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        is_postgres = connection.engine.dialect.name == "postgresql"
        if is_postgres:
            _log.info(
                "Acquiring Postgres advisory lock 0x%x for alembic upgrade",
                _ALEMBIC_ADVISORY_LOCK_KEY,
            )
            # Acquire the lock inside an explicit transaction and commit
            # immediately. The advisory lock is session-scoped (not
            # transaction-scoped) so it survives the commit, but committing
            # leaves the SQLAlchemy connection in a clean no-transaction
            # state — alembic's `context.begin_transaction()` then opens a
            # fresh transaction for the migrations themselves. Without this,
            # SQLA's auto-begin leaves a hanging transaction that confuses
            # alembic's per-migration transaction lifecycle.
            async with connection.begin() as _lock_tx:
                await connection.execute(
                    text("SELECT pg_advisory_lock(:key)"),
                    {"key": _ALEMBIC_ADVISORY_LOCK_KEY},
                )
                await _lock_tx.commit()
        try:
            await connection.run_sync(do_run_migrations)
        finally:
            if is_postgres:
                # Release explicitly via a fresh transaction. The lock would
                # also drop on session close (NullPool means each connection
                # is closed after use) but releasing here makes the intent
                # clear and decouples correctness from pool behaviour.
                try:
                    async with connection.begin() as _unlock_tx:
                        await connection.execute(
                            text("SELECT pg_advisory_unlock(:key)"),
                            {"key": _ALEMBIC_ADVISORY_LOCK_KEY},
                        )
                        await _unlock_tx.commit()
                except Exception as exc:  # pragma: no cover — defensive
                    _log.warning(
                        "Advisory lock release failed (will drop on connection close): %s",
                        exc,
                    )

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
