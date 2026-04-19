"""Migration round-trip test for the email_delivery table.

Verifies:
- alembic upgrade head runs cleanly
- alembic downgrade -1 removes the table and indexes
- alembic upgrade head again re-creates everything

The test runs Alembic in a subprocess to avoid asyncio event-loop conflicts
between the alembic env.py's asyncio.run() and pytest-asyncio's per-test loop.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect


def _backend_root() -> str:
    """Return the absolute path to the backend/ directory."""
    return os.path.dirname(
        os.path.dirname(  # tests/
            os.path.dirname(  # tests/services/
                os.path.dirname(  # tests/services/email/
                    os.path.abspath(__file__)
                )
            )
        )
    )


class TestEmailDeliveryMigration:
    def test_upgrade_downgrade_upgrade_roundtrip(self, tmp_path):
        db_path = str(tmp_path / "migration_test.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"
        sync_url = f"sqlite:///{db_path}"
        root = _backend_root()

        # Build env for subprocess — inherit current env, override DATABASE_URL
        env = os.environ.copy()
        env["DATABASE_URL"] = db_url
        # Ensure ENCRYPTION_KEY and JWT_SECRET_KEY are set
        if "ENCRYPTION_KEY" not in env:
            from cryptography.fernet import Fernet
            env["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
        if "JWT_SECRET_KEY" not in env:
            env["JWT_SECRET_KEY"] = "migration-test-jwt-secret"
        if "ADMIN_USERNAME" not in env:
            env["ADMIN_USERNAME"] = "test-admin"
        if "ADMIN_PASSWORD" not in env:
            env["ADMIN_PASSWORD"] = "Test-Admin-P4ss!"

        def _alembic(*args: str) -> None:
            result = subprocess.run(
                [sys.executable, "-m", "alembic", *args],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise AssertionError(
                    f"alembic {' '.join(args)} failed:\n"
                    f"STDOUT: {result.stdout}\n"
                    f"STDERR: {result.stderr}"
                )

        # --- Upgrade to 0009 (email delivery head for this suite) ---
        _alembic("upgrade", "0009")

        engine = create_engine(sync_url)
        insp = inspect(engine)
        tables = insp.get_table_names()
        assert "email_delivery" in tables, (
            f"email_delivery table not created by upgrade. Tables: {tables}"
        )

        # Verify key columns exist
        columns = {c["name"] for c in insp.get_columns("email_delivery")}
        for col in [
            "id", "created_at", "updated_at", "category", "backend",
            "to_hash", "to_domain", "to_addr", "subject", "status",
            "attempts", "max_attempts", "last_error_code", "last_error_msg",
            "provider_message_id", "idempotency_key", "claimed_by",
            "claim_expires_at", "next_attempt_at", "sent_at",
        ]:
            assert col in columns, f"Column {col!r} missing from email_delivery"

        # Verify indexes exist
        index_names = {idx["name"] for idx in insp.get_indexes("email_delivery")}
        assert "ix_email_delivery_status_next_attempt" in index_names
        assert "ix_email_delivery_status_claim_expires" in index_names

        engine.dispose()

        # --- Downgrade one step ---
        _alembic("downgrade", "-1")

        engine2 = create_engine(sync_url)
        insp2 = inspect(engine2)
        tables_after = insp2.get_table_names()
        assert "email_delivery" not in tables_after, (
            "email_delivery still exists after downgrade"
        )
        engine2.dispose()

        # --- Upgrade again ---
        _alembic("upgrade", "0009")

        engine3 = create_engine(sync_url)
        insp3 = inspect(engine3)
        assert "email_delivery" in insp3.get_table_names(), (
            "email_delivery not recreated on second upgrade"
        )
        engine3.dispose()
