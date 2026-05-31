"""Add optional label to notification_subscription (SFBL-354).

Adds a nullable ``label`` column so users can give a subscription a
human-readable name — opaque webhook / Teams Workflows URLs are otherwise
indistinguishable in the list. Nullable with no backfill: existing rows and
email subscriptions keep working with a null label.

Revision ID: 0031
Revises: 0030
Create Date: 2026-05-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("notification_subscription") as batch:
        batch.add_column(
            sa.Column("label", sa.String(length=120), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_subscription") as batch:
        batch.drop_column("label")
