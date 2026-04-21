"""Create login_attempts table (SFBL-189).

Per-attempt audit log for authentication events. Records every login attempt
with the submitted username, IP, user-agent, outcome, and a nullable FK to the
resolved user (NULL when username was not found).

Revision ID: 0016
Revises: 0015
Create Date: 2026-04-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "login_attempt",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("ip", sa.String(45), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(50), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_login_attempt_attempted_at",
        "login_attempt",
        ["attempted_at"],
    )
    op.create_index(
        "ix_login_attempt_user_id",
        "login_attempt",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_login_attempt_user_id", table_name="login_attempt")
    op.drop_index("ix_login_attempt_attempted_at", table_name="login_attempt")
    op.drop_table("login_attempt")
