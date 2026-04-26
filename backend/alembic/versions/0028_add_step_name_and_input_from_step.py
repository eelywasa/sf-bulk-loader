"""Add LoadStep.name and LoadStep.input_from_step_id (SFBL-261).

Adds two nullable columns and a partial unique index that lets a downstream
DML step reference an upstream query step's run-scoped output by name. This
is the schema half of SFBL-166 (named step outputs / cross-step references).

- ``name`` (VARCHAR 255, nullable) is the optional human-readable identifier
  used by downstream steps to refer to this step's output.
- ``input_from_step_id`` (VARCHAR 36, nullable, FK → ``load_step.id`` with
  ``ON DELETE SET NULL``) wires a step's input source to an earlier step in
  the same plan. Mutually exclusive with ``csv_file_pattern`` and with
  ``input_connection_id`` (enforced at the schema/service layer, not the DB).
- ``uq_load_step_plan_name`` is a partial unique index on
  ``(load_plan_id, name)`` covering only rows where ``name IS NOT NULL`` so
  multiple unnamed steps can coexist within one plan.

Revision ID: 0028
Revises: 0027
Create Date: 2026-04-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("load_step") as batch:
        batch.add_column(sa.Column("name", sa.String(length=255), nullable=True))
        batch.add_column(
            sa.Column(
                "input_from_step_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "load_step.id",
                    ondelete="SET NULL",
                    name="fk_load_step_input_from_step_id",
                ),
                nullable=True,
            )
        )

    op.create_index(
        "uq_load_step_plan_name",
        "load_step",
        ["load_plan_id", "name"],
        unique=True,
        sqlite_where=sa.text("name IS NOT NULL"),
        postgresql_where=sa.text("name IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_load_step_plan_name", table_name="load_step")
    with op.batch_alter_table("load_step") as batch:
        batch.drop_column("input_from_step_id")
        batch.drop_column("name")
