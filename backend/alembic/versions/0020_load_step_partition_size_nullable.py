"""Make load_step.partition_size nullable (SFBL-156).

New LoadStep rows without an explicit partition_size will have NULL, which the
orchestrator resolves at run-time from the DB-backed default_partition_size
setting.  Existing rows with non-NULL values are left untouched — they continue
to use whatever size was set when the step was created.

Revision ID: 0020
Revises: 0019
Create Date: 2026-04-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite requires batch_alter_table to change column nullability.
    with op.batch_alter_table("load_step", recreate="auto") as batch_op:
        batch_op.alter_column(
            "partition_size",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    # Before reverting to NOT NULL, fill any NULLs with the old default so we
    # don't violate the constraint.
    op.execute("UPDATE load_step SET partition_size = 10000 WHERE partition_size IS NULL")
    with op.batch_alter_table("load_step", recreate="auto") as batch_op:
        batch_op.alter_column(
            "partition_size",
            existing_type=sa.Integer(),
            nullable=False,
        )
