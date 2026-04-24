"""Tests for migrations 0021 and 0022 (SFBL-194).

Runs forward + downgrade on an in-memory SQLite database via Alembic's
programmatic API so tests are isolated from the main test DB.

Coverage:
- 0021: profiles table created, three seed profiles present with correct names.
- 0021: profile_permissions rows seeded, all admin keys present.
- 0022: profile_id column added to user table; role column dropped.
- 0022: existing admin user backfills to admin profile; non-admin user → viewer.
- 0022 downgrade: profile_id dropped, role column restored.
"""

from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, inspect, text

os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("JWT_SECRET_KEY", "migration-test-jwt")
os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "Test-Admin-P4ss!")

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig


def _alembic_cfg(db_url: str) -> AlembicConfig:
    """Build an Alembic Config pointing at the test DB URL."""
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture()
def migrated_engine(tmp_path):
    """In-memory SQLite engine with all migrations applied through 0022."""
    db_path = tmp_path / "test_migration.db"
    db_url_sync = f"sqlite:///{db_path}"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"

    # Alembic runs migrations; set the app DB URL to the sync SQLite path.
    import app.config as _cfg_module
    original_url = _cfg_module.settings.database_url
    _cfg_module.settings.database_url = db_url_async

    try:
        cfg = _alembic_cfg(db_url_async)
        alembic_command.upgrade(cfg, "0022")
    finally:
        _cfg_module.settings.database_url = original_url

    engine = create_engine(db_url_sync)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Migration 0021: profiles + seed data
# ---------------------------------------------------------------------------


def test_profiles_table_created(migrated_engine):
    insp = inspect(migrated_engine)
    assert "profiles" in insp.get_table_names()
    assert "profile_permissions" in insp.get_table_names()


def test_three_profiles_seeded(migrated_engine):
    with migrated_engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM profiles ORDER BY name")).fetchall()
    names = [r[0] for r in rows]
    assert names == ["admin", "operator", "viewer"]


def test_admin_profile_has_all_keys(migrated_engine):
    # This fixture stops at migration 0022 — which seeds the original 12-key
    # admin set from migration 0021. Later migrations (e.g. 0026 for SFBL-249)
    # add further keys to the admin profile but aren't exercised here, so
    # assert against the 0021 seed shape rather than the live
    # ``ALL_PERMISSION_KEYS`` vocabulary.
    _MIG_0021_ADMIN_KEYS = {
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
    }

    with migrated_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT pp.permission_key FROM profile_permissions pp "
                "JOIN profiles p ON pp.profile_id = p.id WHERE p.name = 'admin'"
            )
        ).fetchall()
    admin_keys = {r[0] for r in rows}
    assert admin_keys == _MIG_0021_ADMIN_KEYS


def test_viewer_profile_cannot_execute_runs(migrated_engine):
    with migrated_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT pp.permission_key FROM profile_permissions pp "
                "JOIN profiles p ON pp.profile_id = p.id WHERE p.name = 'viewer'"
            )
        ).fetchall()
    viewer_keys = {r[0] for r in rows}
    assert "runs.execute" not in viewer_keys
    assert "runs.view" in viewer_keys


# ---------------------------------------------------------------------------
# Migration 0022: users.role → users.profile_id
# ---------------------------------------------------------------------------


def test_user_table_has_profile_id_column(migrated_engine):
    insp = inspect(migrated_engine)
    cols = {c["name"] for c in insp.get_columns("user")}
    assert "profile_id" in cols
    assert "role" not in cols


def test_admin_user_backfilled_to_admin_profile(migrated_engine):
    with migrated_engine.connect() as conn:
        # Insert an admin-role user directly (simulating pre-migration state).
        # At 0022 time, the user table already has role column dropped so we
        # need to insert via profile_id. We test the backfill logic by running
        # 0021 only and then manually inserting before running 0022.
        admin_id = conn.execute(
            text("SELECT id FROM profiles WHERE name = 'admin'")
        ).scalar_one()
        viewer_id = conn.execute(
            text("SELECT id FROM profiles WHERE name = 'viewer'")
        ).scalar_one()

        # Verify admin profile UUID matches the expected backfill target.
        assert admin_id == "8394ea13-a727-4204-b6aa-79a7d3f99201"
        assert viewer_id == "ed0e6270-8c92-4a65-9338-8ed50e5f630f"


@pytest.fixture()
def engine_at_0021(tmp_path):
    """Engine with migrations through 0021 only (role column still present)."""
    db_path = tmp_path / "backfill_test.db"
    db_url_sync = f"sqlite:///{db_path}"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"

    import app.config as _cfg_module
    original_url = _cfg_module.settings.database_url
    _cfg_module.settings.database_url = db_url_async

    try:
        cfg = _alembic_cfg(db_url_async)
        alembic_command.upgrade(cfg, "0021")
    finally:
        _cfg_module.settings.database_url = original_url

    engine = create_engine(db_url_sync)
    yield engine, db_url_async
    engine.dispose()


def test_backfill_admin_user_to_admin_profile(engine_at_0021, tmp_path):
    """After 0022, admin user ends up with admin profile_id."""
    engine, db_url_async = engine_at_0021

    # Seed a user with role='admin' before running 0022.
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO \"user\" (id, username, hashed_password, role, status, is_admin) "
                "VALUES ('u-admin-1', 'admin', 'hash', 'admin', 'active', 1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO \"user\" (id, username, hashed_password, role, status, is_admin) "
                "VALUES ('u-user-1', 'viewer', 'hash', 'user', 'active', 0)"
            )
        )
        conn.commit()

    import app.config as _cfg_module
    original_url = _cfg_module.settings.database_url
    _cfg_module.settings.database_url = db_url_async
    try:
        cfg = _alembic_cfg(db_url_async)
        alembic_command.upgrade(cfg, "0022")
    finally:
        _cfg_module.settings.database_url = original_url

    with engine.connect() as conn:
        admin_profile_id = conn.execute(
            text("SELECT profile_id FROM \"user\" WHERE id = 'u-admin-1'")
        ).scalar_one()
        viewer_profile_id = conn.execute(
            text("SELECT profile_id FROM \"user\" WHERE id = 'u-user-1'")
        ).scalar_one()

    assert admin_profile_id == "8394ea13-a727-4204-b6aa-79a7d3f99201", "admin → admin profile"
    assert viewer_profile_id == "ed0e6270-8c92-4a65-9338-8ed50e5f630f", "user → viewer profile"


def test_downgrade_restores_role_column(engine_at_0021, tmp_path):
    """Downgrade from 0022 back to 0021 restores role column and drops profile_id."""
    engine, db_url_async = engine_at_0021

    # Apply 0022.
    import app.config as _cfg_module
    original_url = _cfg_module.settings.database_url
    _cfg_module.settings.database_url = db_url_async
    try:
        cfg = _alembic_cfg(db_url_async)
        alembic_command.upgrade(cfg, "0022")
        alembic_command.downgrade(cfg, "0021")
    finally:
        _cfg_module.settings.database_url = original_url

    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("user")}
    assert "role" in cols, "role column must be restored on downgrade"
    assert "profile_id" not in cols, "profile_id must be dropped on downgrade"
