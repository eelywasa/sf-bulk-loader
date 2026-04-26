import logging

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

_log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_is_sqlite = settings.database_url.startswith("sqlite")

# SQLite with WAL mode handles concurrent file access natively — NullPool gives
# each session its own connection and avoids pool exhaustion under parallel jobs.
# PostgreSQL uses a real connection pool sized via DB_POOL_SIZE / DB_POOL_MAX_OVERFLOW.
_pool_kwargs: dict = (
    {"poolclass": NullPool}
    if _is_sqlite
    else {"pool_size": settings.db_pool_size, "max_overflow": settings.db_pool_max_overflow}
)

engine = create_async_engine(
    settings.database_url,
    echo=settings.app_env == "development",
    **_pool_kwargs,
)
_log.info("Database engine: %s", engine.dialect.name)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection: object, _connection_record: object) -> None:
    """Apply per-connection PRAGMAs for SQLite.

    Gate by dialect name rather than ``isinstance(dbapi_connection,
    sqlite3.Connection)``: the ``aiosqlite`` driver wraps the underlying
    sqlite3 connection in its own adapter, so the isinstance check used to
    silently skip — leaving WAL mode off and (critically) ``foreign_keys=OFF``
    so every ``ON DELETE CASCADE`` / ``SET NULL`` declared in the schema was
    a no-op at runtime.
    """
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def assert_sqlite_fk_enforcement_active() -> None:
    """Re-check ``PRAGMA foreign_keys`` on a fresh connection at startup.

    The connect-event listener above is the primary mechanism for turning FK
    enforcement on, but it is silent on failure: a misnamed pragma, a broken
    listener registration, or a regression of the dialect-name gate would
    leave SQLite running with ``foreign_keys=OFF`` and every ``CASCADE`` /
    ``SET NULL`` declared in the schema as a runtime no-op (SFBL-166 / -270).

    This function opens a fresh session, queries the live PRAGMA, and raises
    ``RuntimeError`` if it returns 0. Wired into the FastAPI lifespan so a
    misconfiguration fails the process instead of silently corrupting data.

    No-op for non-SQLite engines — Postgres always enforces FKs.
    """
    if engine.dialect.name != "sqlite":
        return
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("PRAGMA foreign_keys"))
        value = result.scalar()
    if value != 1:
        _log.critical(
            "SQLite PRAGMA foreign_keys is OFF (got %r). FK enforcement is "
            "disabled and every ON DELETE CASCADE / SET NULL in the schema is "
            "a no-op. Refusing to start.",
            value,
        )
        raise RuntimeError(
            f"SQLite foreign_keys enforcement is OFF (PRAGMA returned {value!r}). "
            "Check app.database._set_sqlite_pragma — the connect-event listener "
            "must apply 'PRAGMA foreign_keys=ON' on every new connection."
        )
