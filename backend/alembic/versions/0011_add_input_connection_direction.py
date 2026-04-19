"""Add direction column to input_connection

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("input_connection") as batch_op:
        batch_op.add_column(
            sa.Column(
                "direction",
                sa.String(10),
                nullable=False,
                server_default="in",
            )
        )
        batch_op.create_check_constraint(
            "ck_input_connection_direction",
            "direction IN ('in', 'out', 'both')",
        )


def downgrade() -> None:
    with op.batch_alter_table("input_connection") as batch_op:
        batch_op.drop_constraint("ck_input_connection_direction", type_="check")
        batch_op.drop_column("direction")
