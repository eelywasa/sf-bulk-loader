"""add total_records to job_record

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-16 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("job_record", sa.Column("total_records", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_record", "total_records")
