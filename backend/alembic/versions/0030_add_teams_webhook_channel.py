"""Add teams_webhook value to notification_channel_enum on Postgres (SFBL-352).

Adds the ``teams_webhook`` channel for MS Teams (Power Automate Workflows)
run-complete notifications. Migration 0015 created the ``channel`` column with
the default ``create_constraint=False``, so on SQLite it is a plain VARCHAR
with no CHECK constraint and needs no DDL change. Only the Postgres native
enum type must be extended.

Revision ID: 0030
Revises: 0029
Create Date: 2026-05-31
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # ALTER TYPE ADD VALUE cannot run inside a transaction block on older
    # Postgres versions, and alembic wraps each migration in one. Use
    # autocommit_block to escape it. IF NOT EXISTS makes the migration
    # idempotent on databases where the value already happens to be present.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE notification_channel_enum "
            "ADD VALUE IF NOT EXISTS 'teams_webhook'"
        )


def downgrade() -> None:
    # Postgres has no ALTER TYPE … DROP VALUE. Removing enum values requires
    # recreating the type and rewriting every dependent column, which is not
    # safe for a downgrade path. Leave the value in place.
    pass
