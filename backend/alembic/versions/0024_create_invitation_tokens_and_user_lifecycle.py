"""Create invitation_tokens table and add user lifecycle columns (SFBL-199).

Changes
-------
1. New table ``invitation_tokens`` — stores SHA-256 hashed invitation tokens.
   Columns: id, user_id (FK → user.id), token_hash (unique), created_at,
   expires_at, used_at.  Status (pending / used / expired) is derived from
   timestamps; it is NOT stored as a column.

2. Adds three nullable columns to ``user``:
   - ``invited_by``    FK → user.id (SET NULL), nullable — who created the invite.
   - ``invited_at``    TIMESTAMP nullable — when the invitation was issued.
   - ``last_login_at`` TIMESTAMP nullable — stamped on every successful login.

Revision ID: 0024
Revises: 0023
Create Date: 2026-04-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Create invitation_tokens table ─────────────────────────────────────
    op.create_table(
        "invitation_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_invitation_tokens_expires_used",
        "invitation_tokens",
        ["expires_at", "used_at"],
    )

    # ── 2. Add user lifecycle columns ─────────────────────────────────────────
    # recreate="auto" (default) — Postgres gets straight ALTERs; SQLite only
    # recreates when necessary (avoids breaking FK dependents per SFBL-186 lesson).
    with op.batch_alter_table("user", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column("invited_by", sa.String(36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_user_invited_by",
            "user",
            ["invited_by"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.add_column(
            sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    # ── Remove user lifecycle columns ──────────────────────────────────────────
    with op.batch_alter_table("user", recreate="auto") as batch_op:
        batch_op.drop_column("last_login_at")
        batch_op.drop_column("invited_at")
        batch_op.drop_constraint("fk_user_invited_by", type_="foreignkey")
        batch_op.drop_column("invited_by")

    # ── Drop invitation_tokens table ──────────────────────────────────────────
    op.drop_index("ix_invitation_tokens_expires_used", table_name="invitation_tokens")
    op.drop_table("invitation_tokens")
