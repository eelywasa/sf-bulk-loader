"""add input_connection table and load_step.input_connection_id

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-20 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create input_connection table
    op.create_table(
        "input_connection",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("bucket", sa.String(255), nullable=False),
        sa.Column("root_prefix", sa.String(512), nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("access_key_id", sa.Text, nullable=False),
        sa.Column("secret_access_key", sa.Text, nullable=False),
        sa.Column("session_token", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    # Add input_connection_id to load_step (SQLite guard)
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("load_step")}
    if "input_connection_id" not in existing:
        with op.batch_alter_table("load_step") as batch_op:
            batch_op.add_column(
                sa.Column("input_connection_id", sa.String(36), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_load_step_input_connection_id",
                "input_connection",
                ["input_connection_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_index(
                "ix_load_step_input_connection_id",
                ["input_connection_id"],
            )


def downgrade() -> None:
    with op.batch_alter_table("load_step") as batch_op:
        batch_op.drop_index("ix_load_step_input_connection_id")
        batch_op.drop_constraint("fk_load_step_input_connection_id", type_="foreignkey")
        batch_op.drop_column("input_connection_id")

    op.drop_table("input_connection")
