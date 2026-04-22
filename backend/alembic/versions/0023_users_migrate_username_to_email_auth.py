"""Identity migration — email-based login, drop username (SFBL-198).

- Verifies that all non-deleted users have a non-null email; aborts with a
  clear error message listing any that do not.  This prevents a silent
  data-loss scenario where users would be locked out after the migration.
- Sets the email column NOT NULL + UNIQUE + indexed (it was already nullable
  from migration 0003 which created the table, but may have been added later
  as nullable).
- Drops the username column and its unique index.

Backfill strategy (for pre-SFBL-198 installs):
  The migration refuses to proceed if any non-deleted user is missing an email.
  Operators must backfill email values manually before running this migration.
  The seed_admin function handles the common case (solo admin with no email)
  at startup before migrations by copying ADMIN_EMAIL into the record.

Uses recreate="auto" (NOT "always") so Postgres runs straight ALTERs and
SQLite only recreates the table when needed, avoiding broken FK dependents.
Timestamps use sa.func.now() (not SQLite-specific datetime('now')).

Revision ID: 0023
Revises: 0022
Create Date: 2026-04-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Step 1: Verify all non-deleted users have email ───────────────────────
    # We query via the connection to check for missing emails before touching
    # the schema. This gives a clear error if the operator needs to backfill.
    conn = op.get_bind()
    missing = conn.execute(
        sa.text(
            "SELECT id FROM \"user\""
            " WHERE status != 'deleted' AND (email IS NULL OR email = '')"
        )
    ).fetchall()
    if missing:
        ids = ", ".join(row[0] for row in missing)
        raise RuntimeError(
            f"Identity migration aborted: the following user IDs have no email and must be "
            f"backfilled before running migration 0023: {ids}. "
            f"Set ADMIN_EMAIL in your environment and restart the app once before "
            f"running this migration to let seed_admin backfill the admin account."
        )

    # ── Step 2: Ensure email is NOT NULL + UNIQUE ─────────────────────────────
    # The column already exists (added in migration 0003 as nullable). We
    # recreate the table in one pass to add the NOT NULL constraint and unique
    # index while also dropping the username column.
    with op.batch_alter_table("user", recreate="auto") as batch_op:
        batch_op.alter_column(
            "email",
            existing_type=sa.String(255),
            nullable=False,
        )
        batch_op.create_unique_constraint("uq_user_email", ["email"])
        batch_op.create_index("ix_user_email", ["email"], unique=True)
        batch_op.drop_column("username")


def downgrade() -> None:
    # Restore username column (nullable — values cannot be recovered).
    with op.batch_alter_table("user", recreate="auto") as batch_op:
        batch_op.drop_index("ix_user_email")
        batch_op.drop_constraint("uq_user_email", type_="unique")
        batch_op.alter_column(
            "email",
            existing_type=sa.String(255),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column(
                "username",
                sa.String(255),
                nullable=True,
            )
        )
