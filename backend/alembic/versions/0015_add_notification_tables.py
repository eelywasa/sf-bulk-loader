"""Add notification_subscription and notification_delivery tables (SFBL-179).

Backs run-complete notifications. Two tables:

- notification_subscription: per-user, per-plan (or all plans when plan_id
  is NULL) subscription to terminal run events on one channel/destination.
- notification_delivery: one row per subscription dispatch attempt — NOT per
  HTTP retry. Email retries stay in email_delivery; webhook retries live
  here. /test dispatches set is_test=TRUE and run_id=NULL.

Revision ID: 0015
Revises: 0014
Create Date: 2026-04-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_subscription",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.String(36),
            sa.ForeignKey("load_plan.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "channel",
            sa.Enum("email", "webhook", name="notification_channel_enum"),
            nullable=False,
        ),
        sa.Column("destination", sa.String(512), nullable=False),
        sa.Column(
            "trigger_",
            sa.Enum(
                "terminal_any",
                "terminal_fail_only",
                name="notification_trigger_enum",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id",
            "plan_id",
            "channel",
            "destination",
            name="uq_notification_subscription_user_plan_channel_destination",
        ),
    )

    op.create_table(
        "notification_delivery",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "subscription_id",
            sa.String(36),
            sa.ForeignKey("notification_subscription.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("load_run.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "is_test", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "sent",
                "failed",
                name="notification_delivery_status_enum",
            ),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "email_delivery_id",
            sa.String(36),
            sa.ForeignKey("email_delivery.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_notification_delivery_subscription_run",
        "notification_delivery",
        ["subscription_id", "run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_delivery_subscription_run",
        table_name="notification_delivery",
    )
    op.drop_table("notification_delivery")
    op.drop_table("notification_subscription")
    # Drop enum types explicitly for Postgres (no-op on SQLite).
    bind = op.get_bind()
    sa.Enum(name="notification_delivery_status_enum").drop(bind, checkfirst=True)
    sa.Enum(name="notification_trigger_enum").drop(bind, checkfirst=True)
    sa.Enum(name="notification_channel_enum").drop(bind, checkfirst=True)
