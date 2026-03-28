"""Add last_heartbeat_at to load_run for stuck-run detection (SFBL-59)

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("load_run") as batch_op:
        batch_op.add_column(
            sa.Column(
                "last_heartbeat_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("load_run") as batch_op:
        batch_op.drop_column("last_heartbeat_at")
