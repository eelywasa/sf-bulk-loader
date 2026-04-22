"""Create profiles and profile_permissions tables with seed data (SFBL-194).

Creates:
  - profiles: id (UUID PK), name (unique), description, is_system, created_at
  - profile_permissions: composite PK (profile_id FK + permission_key)

Seeds the three system profiles (admin, operator, viewer) with static UUIDs so
the migration is deterministic and idempotent across environments. Permission
key assignments follow spec §5.2.

Revision ID: 0021
Revises: 0020
Create Date: 2026-04-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Static UUIDs — hardcoded so migrations are deterministic across environments.
_ADMIN_ID = "8394ea13-a727-4204-b6aa-79a7d3f99201"
_OPERATOR_ID = "455f46dd-d814-44cc-b6e7-c53f551c6971"
_VIEWER_ID = "ed0e6270-8c92-4a65-9338-8ed50e5f630f"

# Permission key sets per spec §5.2.
_ADMIN_KEYS = [
    "connections.view",
    "connections.view_credentials",
    "connections.manage",
    "plans.view",
    "plans.manage",
    "runs.view",
    "runs.execute",
    "runs.abort",
    "files.view",
    "files.view_contents",
    "users.manage",
    "system.settings",
]
_OPERATOR_KEYS = [
    "connections.view",
    "plans.view",
    "plans.manage",
    "runs.view",
    "runs.execute",
    "runs.abort",
    "files.view",
    "files.view_contents",
]
_VIEWER_KEYS = [
    "connections.view",
    "plans.view",
    "runs.view",
    "files.view",
]


def upgrade() -> None:
    profiles_table = op.create_table(
        "profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False, unique=True),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "profile_permissions",
        sa.Column(
            "profile_id",
            sa.String(36),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("permission_key", sa.String(100), primary_key=True),
    )

    # Seed the three system profiles.
    op.bulk_insert(
        profiles_table,
        [
            {
                "id": _ADMIN_ID,
                "name": "admin",
                "description": "Full access to all features including user management.",
                "is_system": True,
            },
            {
                "id": _OPERATOR_ID,
                "name": "operator",
                "description": "Day-to-day operations: manage plans, execute runs. Cannot manage users or connection credentials.",
                "is_system": True,
            },
            {
                "id": _VIEWER_ID,
                "name": "viewer",
                "description": "Read-only access. Cannot execute runs or view file contents.",
                "is_system": True,
            },
        ],
    )

    # Seed permission keys using bulk_insert via the profile_permissions table object.
    perm_table = sa.table(
        "profile_permissions",
        sa.column("profile_id", sa.String),
        sa.column("permission_key", sa.String),
    )
    perm_rows = (
        [{"profile_id": _ADMIN_ID, "permission_key": k} for k in _ADMIN_KEYS]
        + [{"profile_id": _OPERATOR_ID, "permission_key": k} for k in _OPERATOR_KEYS]
        + [{"profile_id": _VIEWER_ID, "permission_key": k} for k in _VIEWER_KEYS]
    )
    op.bulk_insert(perm_table, perm_rows)


def downgrade() -> None:
    op.drop_table("profile_permissions")
    op.drop_table("profiles")
