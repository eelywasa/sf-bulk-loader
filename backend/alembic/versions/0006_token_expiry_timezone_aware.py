"""Make token_expiry timezone-aware (TIMESTAMP WITH TIME ZONE)

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("connection") as batch_op:
        batch_op.alter_column(
            "token_expiry",
            type_=sa.DateTime(timezone=True),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("connection") as batch_op:
        batch_op.alter_column(
            "token_expiry",
            type_=sa.DateTime(timezone=False),
            existing_nullable=True,
        )
