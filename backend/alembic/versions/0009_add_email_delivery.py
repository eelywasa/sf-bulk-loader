"""Add email_delivery table for outbound email delivery log (SFBL-138)

Columns include CAS-claim fields (claimed_by, claim_expires_at, next_attempt_at),
recipient privacy model (to_hash, to_domain, to_addr), and idempotency key.

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-17
"""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_delivery",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("template", sa.String(255), nullable=True),
        sa.Column("backend", sa.String(20), nullable=False),
        sa.Column("to_hash", sa.String(64), nullable=False),
        sa.Column("to_domain", sa.String(255), nullable=False),
        sa.Column("to_addr", sa.String(320), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "sending", "sent", "failed", "skipped",
                name="delivery_status_enum",
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("last_error_msg", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True, unique=True),
        sa.Column("claimed_by", sa.String(255), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_email_delivery_status_next_attempt",
        "email_delivery",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_email_delivery_status_claim_expires",
        "email_delivery",
        ["status", "claim_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_delivery_status_claim_expires", table_name="email_delivery")
    op.drop_index("ix_email_delivery_status_next_attempt", table_name="email_delivery")
    op.drop_table("email_delivery")
    # Drop the enum type (no-op on SQLite; required on PostgreSQL)
    sa.Enum(name="delivery_status_enum").drop(op.get_bind(), checkfirst=True)
