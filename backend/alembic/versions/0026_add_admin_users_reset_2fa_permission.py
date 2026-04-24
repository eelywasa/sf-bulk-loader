"""Add admin.users.reset_2fa permission to the admin profile (SFBL-249).

Admins need the ability to clear another user's TOTP factor + backup codes when
the authenticator is lost. Per D8 of docs/specs/2fa-totp.md, when require_2fa is
tenant-enforced users cannot self-disable, so admin reset is the only in-product
recovery path (the CLI break-glass covers the case where no admin can log in).

Revision ID: 0026
Revises: 0025
Create Date: 2026-04-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ADMIN_PROFILE_ID = "8394ea13-a727-4204-b6aa-79a7d3f99201"
_PERMISSION_KEY = "admin.users.reset_2fa"


def upgrade() -> None:
    perm_table = sa.table(
        "profile_permissions",
        sa.column("profile_id", sa.String),
        sa.column("permission_key", sa.String),
    )
    # Idempotent insert — skip if row already exists (re-running against a DB
    # that was hand-seeded or partially migrated).
    bind = op.get_bind()
    existing = bind.execute(
        sa.text(
            "SELECT 1 FROM profile_permissions "
            "WHERE profile_id = :pid AND permission_key = :key"
        ),
        {"pid": _ADMIN_PROFILE_ID, "key": _PERMISSION_KEY},
    ).scalar()
    if existing is None:
        op.bulk_insert(
            perm_table,
            [{"profile_id": _ADMIN_PROFILE_ID, "permission_key": _PERMISSION_KEY}],
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM profile_permissions "
            "WHERE profile_id = :pid AND permission_key = :key"
        ).bindparams(pid=_ADMIN_PROFILE_ID, key=_PERMISSION_KEY)
    )
