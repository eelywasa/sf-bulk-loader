"""Add query/queryAll values to operation_enum on Postgres (SFBL-272).

Migration 0013 added ``query`` and ``queryAll`` to the SQLAlchemy ``Operation``
enum but explicitly skipped the Postgres ``ALTER TYPE … ADD VALUE`` because
the project then targeted SQLite only. SFBL-272 introduces a SQLite → Postgres
migration CLI; without this fix any plan containing a query step fails to
copy across with ``invalid input value for enum operation_enum: "query"``.

SQLite stores enums as VARCHAR so it needs no DDL change.

Revision ID: 0029
Revises: 0028
Create Date: 2026-04-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # ALTER TYPE ADD VALUE cannot run inside a transaction block on older
    # Postgres versions, and alembic wraps each migration in one. Use
    # autocommit_block to escape it. IF NOT EXISTS makes the migration
    # idempotent on databases where the values already happen to be present.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE operation_enum ADD VALUE IF NOT EXISTS 'query'")
        op.execute("ALTER TYPE operation_enum ADD VALUE IF NOT EXISTS 'queryAll'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE … DROP VALUE. Removing enum values requires
    # recreating the type and rewriting every dependent column, which is not
    # safe for a downgrade path. Leave the values in place.
    pass
