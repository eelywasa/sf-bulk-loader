"""Add password_reset_token, email_change_token tables and password_changed_at column (SFBL-145)

Creates two short-lived token tables that back the password-reset and
email-change flows, and adds a JWT-invalidation watermark column to the
user table.

password_reset_token — stores SHA-256 hashes of single-use password-reset
    links; raw tokens are never persisted.

email_change_token — stores SHA-256 hashes of email-change verification
    links together with the target address.

user.password_changed_at — nullable datetime (UTC). Any JWT whose ``iat``
    claim is strictly older than this value is rejected by get_current_user,
    ensuring that tokens issued before a password change cannot be replayed.

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-18
"""

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── password_reset_token ──────────────────────────────────────────────────
    op.create_table(
        "password_reset_token",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_ip", sa.String(45), nullable=True),
    )

    op.create_index(
        "ix_password_reset_token_user_id",
        "password_reset_token",
        ["user_id"],
    )
    op.create_index(
        "ix_password_reset_token_token_hash",
        "password_reset_token",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_password_reset_token_expires_used",
        "password_reset_token",
        ["expires_at", "used_at"],
    )

    # ── email_change_token ────────────────────────────────────────────────────
    op.create_table(
        "email_change_token",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("new_email", sa.String(320), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_index(
        "ix_email_change_token_user_id",
        "email_change_token",
        ["user_id"],
    )
    op.create_index(
        "ix_email_change_token_token_hash",
        "email_change_token",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_email_change_token_expires_used",
        "email_change_token",
        ["expires_at", "used_at"],
    )

    # ── user.password_changed_at ──────────────────────────────────────────────
    op.add_column(
        "user",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill: existing rows get NULL (no watermark — all prior tokens remain valid)


def downgrade() -> None:
    # Remove column from user table
    op.drop_column("user", "password_changed_at")

    # Drop email_change_token
    op.drop_index("ix_email_change_token_expires_used", table_name="email_change_token")
    op.drop_index("ix_email_change_token_token_hash", table_name="email_change_token")
    op.drop_index("ix_email_change_token_user_id", table_name="email_change_token")
    op.drop_table("email_change_token")

    # Drop password_reset_token
    op.drop_index("ix_password_reset_token_expires_used", table_name="password_reset_token")
    op.drop_index("ix_password_reset_token_token_hash", table_name="password_reset_token")
    op.drop_index("ix_password_reset_token_user_id", table_name="password_reset_token")
    op.drop_table("password_reset_token")
