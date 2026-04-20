"""Drop the load_step.input_connection_id FK constraint (SFBL-178)

The column has always been a loosely-typed source identifier — code
treats `None`, `""`, and `"local"` as magic values meaning "local input
tree", and the FK constraint blocks adding further reserved sentinels.
SFBL-178 adds `"local-output"` to route reads to the output directory.

Rather than seed a pseudo-InputConnection row or split the field across
two columns, we drop the FK.  The application layer is responsible for
validating the value (real UUID vs. reserved sentinel) via
`_validate_input_connection_direction` in `api/load_steps.py` and the
`get_storage` resolver.

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("load_step") as batch_op:
        batch_op.drop_constraint(
            "fk_load_step_input_connection_id", type_="foreignkey"
        )


def downgrade() -> None:
    with op.batch_alter_table("load_step") as batch_op:
        batch_op.create_foreign_key(
            "fk_load_step_input_connection_id",
            "input_connection",
            ["input_connection_id"],
            ["id"],
            ondelete="RESTRICT",
        )
