"""Make started_at / completed_at timezone-aware (TIMESTAMP WITH TIME ZONE)

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("load_run") as batch_op:
        batch_op.alter_column(
            "started_at",
            type_=sa.DateTime(timezone=True),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "completed_at",
            type_=sa.DateTime(timezone=True),
            existing_nullable=True,
        )

    with op.batch_alter_table("job_record") as batch_op:
        batch_op.alter_column(
            "started_at",
            type_=sa.DateTime(timezone=True),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "completed_at",
            type_=sa.DateTime(timezone=True),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("load_run") as batch_op:
        batch_op.alter_column(
            "started_at",
            type_=sa.DateTime(timezone=False),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "completed_at",
            type_=sa.DateTime(timezone=False),
            existing_nullable=True,
        )

    with op.batch_alter_table("job_record") as batch_op:
        batch_op.alter_column(
            "started_at",
            type_=sa.DateTime(timezone=False),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "completed_at",
            type_=sa.DateTime(timezone=False),
            existing_nullable=True,
        )
