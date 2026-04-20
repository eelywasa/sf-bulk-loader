"""Add query/queryAll operations, soql column, make csv_file_pattern nullable

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("load_step") as batch_op:
        # Add the soql column (nullable — only populated for query ops)
        batch_op.add_column(
            sa.Column("soql", sa.Text(), nullable=True)
        )
        # Make csv_file_pattern nullable (query ops do not have CSV input)
        batch_op.alter_column(
            "csv_file_pattern",
            existing_type=sa.String(512),
            nullable=True,
        )

    # SQLite stores enums as VARCHAR; the values are added automatically when
    # new rows are inserted.  No DDL change is needed for SQLite.  If using
    # PostgreSQL the enum type would need ALTER TYPE … ADD VALUE statements,
    # but the project targets SQLite for all current deployments.
    # The ORM-level Operation enum already includes query / queryAll so new
    # rows can use those values immediately after this migration.


def downgrade() -> None:
    with op.batch_alter_table("load_step") as batch_op:
        batch_op.alter_column(
            "csv_file_pattern",
            existing_type=sa.String(512),
            nullable=False,
        )
        batch_op.drop_column("soql")
