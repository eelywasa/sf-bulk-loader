"""Add consecutive_failure_threshold to load_plan (SFBL-121).

Circuit-breaker: abort a run after N consecutive partition-level failures
against the same Salesforce instance.  NULL (the default for existing plans)
disables the feature entirely.

Revision ID: 0027
Revises: 0026
Create Date: 2026-04-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "load_plan",
        sa.Column(
            "consecutive_failure_threshold",
            sa.Integer(),
            nullable=True,
            server_default=None,
        ),
    )


def downgrade() -> None:
    op.drop_column("load_plan", "consecutive_failure_threshold")
