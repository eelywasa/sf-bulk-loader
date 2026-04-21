"""Migrate users.role column to users.profile_id FK (SFBL-194).

- Adds profile_id nullable FK → profiles.id.
- Backfills: role='admin' → admin profile; everything else → viewer profile.
- Makes profile_id NOT NULL.
- Drops role column.

Uses recreate="auto" (the default) so Postgres runs straight ALTERs and SQLite
only recreates the table when needed — avoids breaking FK dependents (SFBL-186
lesson: recreate="always" breaks Postgres when dependent tables reference the PK).

Revision ID: 0022
Revises: 0021
Create Date: 2026-04-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must match 0021 seed UUIDs.
_ADMIN_ID = "8394ea13-a727-4204-b6aa-79a7d3f99201"
_VIEWER_ID = "ed0e6270-8c92-4a65-9338-8ed50e5f630f"


def upgrade() -> None:
    # Step 1: add profile_id as nullable (needed for backfill before NOT NULL).
    # The FK constraint must be named for SQLite batch_alter_table compatibility.
    with op.batch_alter_table("user", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column("profile_id", sa.String(36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_user_profile_id",
            "profiles",
            ["profile_id"],
            ["id"],
        )

    # Step 2: backfill. role='admin' → admin profile; everything else → viewer.
    # This covers role='user', NULL, and any other value safely (spec §5.3 line 207).
    op.execute(
        sa.text(
            "UPDATE \"user\" SET profile_id = CASE"
            f"  WHEN role = 'admin' THEN '{_ADMIN_ID}'"
            f"  ELSE '{_VIEWER_ID}'"
            " END"
        )
    )

    # Step 3: make profile_id NOT NULL and drop role.
    with op.batch_alter_table("user", recreate="auto") as batch_op:
        batch_op.alter_column("profile_id", nullable=False)
        batch_op.drop_column("role")


def downgrade() -> None:
    # Restore role column, backfill from is_admin, then drop profile_id.
    with op.batch_alter_table("user", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column(
                "role",
                sa.String(50),
                nullable=True,  # temporarily nullable for backfill
            )
        )

    # Backfill role from is_admin (best-effort; original role='user' rows all become 'user').
    op.execute(
        sa.text("UPDATE \"user\" SET role = CASE WHEN is_admin THEN 'admin' ELSE 'user' END")
    )

    with op.batch_alter_table("user", recreate="auto") as batch_op:
        batch_op.alter_column("role", nullable=False, server_default=sa.text("'user'"))
        batch_op.drop_column("profile_id")
