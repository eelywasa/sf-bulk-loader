"""add retry_of_run_id to load_run

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-17 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite has no ADD COLUMN IF NOT EXISTS; guard manually.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("load_run")}
    if "retry_of_run_id" not in existing:
        op.add_column(
            "load_run",
            sa.Column(
                "retry_of_run_id",
                sa.String(36),
                sa.ForeignKey("load_run.id"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    op.drop_column("load_run", "retry_of_run_id")
