"""Create user_totp and user_backup_code tables (SFBL-245 — 2FA foundation).

Adds the persistence layer for TOTP-based 2FA (epic SFBL-244). No behavioural
change — subsequent tickets wire these tables into enrollment, login, and
admin-reset flows.

Changes
-------
1. New table ``user_totp`` — one row per enrolled user (unique FK ``user_id``).
   Rows are only inserted after the user successfully confirms their first
   code (spec §0 D11 — stateless enrolment). Stores the Fernet-encrypted
   base32 TOTP secret, RFC 6238 parameters (SHA1 / 6 digits / 30s), and
   anti-replay tracking (``last_used_at`` / ``last_used_counter``).

2. New table ``user_backup_code`` — one row per backup code. 10 codes are
   minted at enrolment confirmation (spec §0 D9); each row stores a bcrypt
   hash of the plaintext code and is marked consumed on redemption. Index
   ``ix_user_backup_code_user_consumed`` supports fast "count unconsumed"
   queries.

No change to the ``user`` table: "user must enrol on next login" is derived
from ``user_totp IS NULL AND tenant.require_2fa``.

Revision ID: 0025
Revises: 0024
Create Date: 2026-04-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. user_totp — one row per enrolled user ──────────────────────────────
    op.create_table(
        "user_totp",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "algorithm",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'SHA1'"),
        ),
        sa.Column(
            "digits",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("6"),
        ),
        sa.Column(
            "period_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("30"),
        ),
        sa.Column(
            "enrolled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_counter", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── 2. user_backup_code — one row per minted code ─────────────────────────
    op.create_table(
        "user_backup_code",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(60), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_ip", sa.String(45), nullable=True),
    )
    op.create_index(
        "ix_user_backup_code_user_consumed",
        "user_backup_code",
        ["user_id", "consumed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_backup_code_user_consumed", table_name="user_backup_code"
    )
    op.drop_table("user_backup_code")
    op.drop_table("user_totp")
