"""Add status enum and lockout columns to user table (SFBL-189).

Introduces the user state model:

- status: string column with CHECK constraint ('invited', 'active', 'locked',
  'deactivated', 'deleted'). Backfilled from is_active (true → 'active',
  false → 'deactivated'). Uses portable string+CHECK approach (matches
  migration 0015 pattern) to work on SQLite.
- locked_until: nullable datetime for tier-1 auto-lockout expiry.
- failed_login_count: integer counter, default 0.
- last_failed_login_at: nullable datetime of the most recent failure.
- must_reset_password: boolean flag for temp-password users.

After backfilling status, is_active is dropped.

Revision ID: 0017
Revises: 0016
Create Date: 2026-04-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS_VALUES = ("'invited'", "'active'", "'locked'", "'deactivated'", "'deleted'")
_STATUS_CHECK = f"status IN ({', '.join(_STATUS_VALUES)})"


def upgrade() -> None:
    # Step 1: add the new columns alongside the existing is_active column.
    # status is temporarily nullable to allow backfill before making it NOT NULL.
    with op.batch_alter_table("user", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(20),
                sa.CheckConstraint(_STATUS_CHECK, name="ck_user_status"),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "failed_login_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(
            sa.Column("last_failed_login_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "must_reset_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    # Step 2: backfill status from is_active (SQLite uses 1/0 for booleans).
    op.execute(
        sa.text(
            "UPDATE \"user\" SET status = CASE WHEN is_active = 1 THEN 'active' ELSE 'deactivated' END"
        )
    )

    # Step 3: make status NOT NULL and drop is_active — single batch recreate.
    with op.batch_alter_table("user", recreate="always") as batch_op:
        batch_op.alter_column("status", nullable=False)
        batch_op.drop_column("is_active")


def downgrade() -> None:
    # Downgrade must restore is_active and remove the new columns.
    #
    # We cannot use batch_alter_table here because the SQLAlchemy ORM model
    # for User still has ck_user_status in __table_args__, so any
    # batch_alter_table recreate would include that CHECK constraint on the new
    # table even after status is dropped, causing "no such column: status".
    #
    # Instead, we use raw SQLite DDL to rebuild the user table without the new
    # columns and with is_active restored:
    #   1. Add is_active with a plain ALTER TABLE (SQLite supports this for
    #      nullable columns).
    #   2. Backfill is_active from status.
    #   3. Recreate the table without status/lockout columns using raw CREATE
    #      TABLE AS SELECT + rename pattern.
    bind = op.get_bind()

    # Step 1: add is_active as nullable boolean.
    bind.execute(sa.text('ALTER TABLE "user" ADD COLUMN is_active BOOLEAN'))

    # Step 2: backfill is_active from status.
    bind.execute(
        sa.text(
            "UPDATE \"user\" SET is_active = CASE WHEN status = 'active' THEN 1 ELSE 0 END"
        )
    )

    # Step 3: recreate the user table without the new columns.
    # We create a temp table with the target schema, copy rows, drop the old
    # table, and rename the temp table.  is_active is set NOT NULL here.
    bind.execute(sa.text("""
        CREATE TABLE _alembic_downgrade_user (
            id VARCHAR(36) NOT NULL,
            username VARCHAR(255),
            hashed_password VARCHAR(255),
            email VARCHAR(255),
            display_name VARCHAR(255),
            saml_name_id VARCHAR(512),
            is_active BOOLEAN NOT NULL,
            role VARCHAR(50) NOT NULL DEFAULT 'user',
            created_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            updated_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            password_changed_at DATETIME,
            PRIMARY KEY (id),
            UNIQUE (username)
        )
    """))
    bind.execute(sa.text("""
        INSERT INTO _alembic_downgrade_user
            (id, username, hashed_password, email, display_name, saml_name_id,
             is_active, role, created_at, updated_at, password_changed_at)
        SELECT  id, username, hashed_password, email, display_name, saml_name_id,
                is_active, role, created_at, updated_at, password_changed_at
        FROM "user"
    """))
    bind.execute(sa.text('DROP TABLE "user"'))
    bind.execute(sa.text('ALTER TABLE _alembic_downgrade_user RENAME TO "user"'))
