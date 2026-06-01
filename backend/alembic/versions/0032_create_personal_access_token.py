"""Create personal_access_token table (SFBL-366 — PAT data model).

Adds the persistence layer for Personal Access Tokens. No behavioural change —
subsequent tickets (SFBL-367, SFBL-368) wire the table into the auth middleware
and management API respectively.

Changes
-------
New table ``personal_access_token``:
  - ``id``            — UUID PK (String 36), consistent with all other models
  - ``user_id``       — FK → user.id with CASCADE delete; indexed
  - ``name``          — human-readable label supplied at issuance
  - ``token_hash``    — HMAC-SHA256 hex digest (64 chars); unique + indexed for O(1) lookup
  - ``prefix``        — fixed display prefix (e.g. ``sfbl_pat_``)
  - ``last4``         — last 4 chars of the plaintext; safe for display
  - ``created_at``    — issuance timestamp; server_default=now()
  - ``last_used_at``  — nullable; updated by SFBL-367 on each successful auth
  - ``expires_at``    — nullable; NULL means non-expiring
  - ``revoked_at``    — nullable; set on explicit revocation
  - ``scope``         — nullable Text (forward-compat; NOT enforced in v1)

Indexes:
  - ``ix_pat_token_hash``  — unique; supports O(1) hash lookup (implicit from unique=True)
  - ``ix_pat_user_id``     — supports per-user list queries (implicit from index=True)
  - ``ix_pat_user_created`` — composite (user_id, created_at) for sorted list queries

No backfill required — new table only.

Uses generic SQLAlchemy types (sa.String, sa.Text, sa.DateTime) so the migration
applies cleanly on both SQLite and Postgres.  No SQLite-only or PG-only constructs
are used.  ``server_default`` on ``created_at`` uses ``sa.func.now()`` which is
safe on both backends.

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "personal_access_token",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        # HMAC-SHA256 hex digest — 64 hex characters
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        # Fixed display prefix stored for echo-back (no plaintext stored)
        sa.Column("prefix", sa.String(16), nullable=False),
        # Last 4 chars of the plaintext token — safe display aid
        sa.Column("last4", sa.String(4), nullable=False),
        # Issuance timestamp
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Nullable activity / lifecycle timestamps
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        # Forward-compat scope — Text for portability (no native JSON required)
        sa.Column("scope", sa.Text(), nullable=True),
    )

    # Explicit named indexes for clarity and cross-dialect safety
    op.create_index(
        "ix_pat_user_id",
        "personal_access_token",
        ["user_id"],
    )
    op.create_index(
        "ix_pat_user_created",
        "personal_access_token",
        ["user_id", "created_at"],
    )
    # token_hash unique index is created by the unique=True constraint above;
    # alembic emits it automatically.  A named alias is added here so downgrade
    # can reference it explicitly on both backends.
    op.create_index(
        "ix_pat_token_hash",
        "personal_access_token",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_pat_token_hash", table_name="personal_access_token")
    op.drop_index("ix_pat_user_created", table_name="personal_access_token")
    op.drop_index("ix_pat_user_id", table_name="personal_access_token")
    op.drop_table("personal_access_token")
