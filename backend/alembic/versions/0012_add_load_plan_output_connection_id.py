"""Add output_connection_id FK to load_plan

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("load_plan") as batch_op:
        batch_op.add_column(
            sa.Column("output_connection_id", sa.String(36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_load_plan_output_connection_id",
            "input_connection",
            ["output_connection_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_load_plan_output_connection_id",
            ["output_connection_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("load_plan") as batch_op:
        batch_op.drop_index("ix_load_plan_output_connection_id")
        batch_op.drop_constraint("fk_load_plan_output_connection_id", type_="foreignkey")
        batch_op.drop_column("output_connection_id")
