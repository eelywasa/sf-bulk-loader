"""Add tokens.manage and notifications.manage permission keys (SFBL-368 / SFBL-374).

Grants ``tokens.manage`` and ``notifications.manage`` to all three system
profiles (admin, operator, viewer).  Idempotent — re-running on a DB that
already has the rows is safe.

Rationale
---------
- ``tokens.manage`` gates the PAT management API (SFBL-368): any authenticated
  user should be able to manage their own tokens.
- ``notifications.manage`` formalises the previously unenforced notification
  subscription routes (SFBL-374 audit follow-up): any authenticated user should
  be able to manage their own notification subscriptions.

Both keys are granted to all profiles because they govern *self-service* data
(own tokens / own subscriptions).  Restricting them to admin-only would prevent
operators and viewers from using basic self-service features.

Revision ID: 0033
Revises: 0032
Create Date: 2026-06-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Static UUIDs — match migration 0021 seed data
_ADMIN_PROFILE_ID = "8394ea13-a727-4204-b6aa-79a7d3f99201"
_OPERATOR_PROFILE_ID = "455f46dd-d814-44cc-b6e7-c53f551c6971"
_VIEWER_PROFILE_ID = "ed0e6270-8c92-4a65-9338-8ed50e5f630f"

_NEW_KEYS = ["tokens.manage", "notifications.manage"]
_PROFILE_IDS = [_ADMIN_PROFILE_ID, _OPERATOR_PROFILE_ID, _VIEWER_PROFILE_ID]

_PERM_TABLE = sa.table(
    "profile_permissions",
    sa.column("profile_id", sa.String),
    sa.column("permission_key", sa.String),
)


def upgrade() -> None:
    bind = op.get_bind()
    for profile_id in _PROFILE_IDS:
        for key in _NEW_KEYS:
            existing = bind.execute(
                sa.text(
                    "SELECT 1 FROM profile_permissions "
                    "WHERE profile_id = :pid AND permission_key = :key"
                ),
                {"pid": profile_id, "key": key},
            ).scalar()
            if existing is None:
                op.bulk_insert(
                    _PERM_TABLE,
                    [{"profile_id": profile_id, "permission_key": key}],
                )


def downgrade() -> None:
    bind = op.get_bind()
    for profile_id in _PROFILE_IDS:
        for key in _NEW_KEYS:
            bind.execute(
                sa.text(
                    "DELETE FROM profile_permissions "
                    "WHERE profile_id = :pid AND permission_key = :key"
                ),
                {"pid": profile_id, "key": key},
            )
