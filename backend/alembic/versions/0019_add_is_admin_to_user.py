"""Add is_admin boolean column to user table (SFBL-154).

Backfills existing rows: any user with role='admin' gets is_admin=TRUE.
Uses batch_alter_table for SQLite compatibility. PostgreSQL uses the same
path safely (batch mode on PG is a no-op wrapper).

Revision ID: 0019
Revises: 0018
Create Date: 2026-04-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_admin",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    # Backfill: mark existing admin-role users as is_admin=TRUE
    op.execute('UPDATE "user" SET is_admin = TRUE WHERE role = \'admin\'')


def downgrade() -> None:
    with op.batch_alter_table("user", recreate="auto") as batch_op:
        batch_op.drop_column("is_admin")
