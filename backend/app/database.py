import logging
import sqlite3

from sqlalchemy import event
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
def _set_sqlite_pragma(dbapi_connection: sqlite3.Connection, _connection_record: object) -> None:
    """Enable WAL mode for better concurrent read/write performance."""
    if isinstance(dbapi_connection, sqlite3.Connection):
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
