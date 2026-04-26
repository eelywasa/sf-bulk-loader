"""SFBL-156: Verify registry entries and orchestrator fallback for migrated settings.

Tests:
- SETTINGS_REGISTRY contains all expected salesforce/partitioning/security keys
- Keys have correct types, categories, and defaults
- step_executor uses default_partition_size from settings_service when
  LoadStep.partition_size is None
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.settings.registry import SETTINGS_REGISTRY, SettingMeta


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

_EXPECTED_SALESFORCE_KEYS = {
    "sf_api_version",
    "sf_poll_interval_initial",
    "sf_poll_interval_max",
    "sf_job_timeout_minutes",
    "sf_job_max_poll_seconds",
}

_EXPECTED_PARTITIONING_KEYS = {
    "default_partition_size",
    "max_partition_size",
}

_EXPECTED_SECURITY_EXTRA_KEYS = {
    "jwt_expiry_minutes",
    "pw_reset_rate_limit_per_ip_hour",
    "pw_reset_rate_limit_per_email_hour",
    "email_change_rate_limit_per_user_hour",
    "password_reset_ttl_minutes",
    "email_change_ttl_minutes",
}

_EXPECTED_SECURITY_LOCKOUT_KEYS = {
    "login_rate_limit_attempts",
    "login_rate_limit_window_seconds",
    "login_tier1_threshold",
    "login_tier1_window_minutes",
    "login_tier1_lock_minutes",
    "login_tier2_threshold",
    "login_tier2_tier1_count",
    "login_tier2_window_hours",
}


class TestRegistrySalesforceKeys:
    def test_all_salesforce_keys_present(self) -> None:
        assert _EXPECTED_SALESFORCE_KEYS.issubset(set(SETTINGS_REGISTRY.keys()))

    def test_salesforce_keys_have_correct_category(self) -> None:
        for key in _EXPECTED_SALESFORCE_KEYS:
            assert SETTINGS_REGISTRY[key].category == "salesforce", (
                f"{key} should have category='salesforce'"
            )

    def test_salesforce_keys_have_correct_types(self) -> None:
        expected_types = {
            "sf_api_version": "str",
            "sf_poll_interval_initial": "int",
            "sf_poll_interval_max": "int",
            "sf_job_timeout_minutes": "int",
            "sf_job_max_poll_seconds": "int",
        }
        for key, expected_type in expected_types.items():
            assert SETTINGS_REGISTRY[key].type == expected_type, (
                f"{key} should have type={expected_type!r}"
            )

    def test_salesforce_keys_are_not_secret(self) -> None:
        for key in _EXPECTED_SALESFORCE_KEYS:
            assert not SETTINGS_REGISTRY[key].is_secret, f"{key} should not be is_secret"

    def test_salesforce_keys_have_env_vars(self) -> None:
        for key in _EXPECTED_SALESFORCE_KEYS:
            assert SETTINGS_REGISTRY[key].env_var, f"{key} should have an env_var"

    def test_salesforce_key_defaults(self) -> None:
        expected = {
            "sf_api_version": "v62.0",
            "sf_poll_interval_initial": 5,
            "sf_poll_interval_max": 30,
            "sf_job_timeout_minutes": 30,
            "sf_job_max_poll_seconds": 3600,
        }
        for key, default in expected.items():
            assert SETTINGS_REGISTRY[key].default == default, (
                f"{key}: expected default {default!r}, got {SETTINGS_REGISTRY[key].default!r}"
            )


class TestRegistryPartitioningKeys:
    def test_all_partitioning_keys_present(self) -> None:
        assert _EXPECTED_PARTITIONING_KEYS.issubset(set(SETTINGS_REGISTRY.keys()))

    def test_partitioning_keys_have_correct_category(self) -> None:
        for key in _EXPECTED_PARTITIONING_KEYS:
            assert SETTINGS_REGISTRY[key].category == "partitioning", (
                f"{key} should have category='partitioning'"
            )

    def test_partitioning_keys_are_int_type(self) -> None:
        for key in _EXPECTED_PARTITIONING_KEYS:
            assert SETTINGS_REGISTRY[key].type == "int", f"{key} should have type='int'"

    def test_partitioning_key_defaults(self) -> None:
        expected = {
            "default_partition_size": 10_000,
            "max_partition_size": 100_000_000,
        }
        for key, default in expected.items():
            assert SETTINGS_REGISTRY[key].default == default, (
                f"{key}: expected default {default!r}, got {SETTINGS_REGISTRY[key].default!r}"
            )


class TestRegistrySecurityKeys:
    def test_lockout_keys_still_present(self) -> None:
        """SFBL-153 lockout keys must not have been removed by SFBL-156."""
        assert _EXPECTED_SECURITY_LOCKOUT_KEYS.issubset(set(SETTINGS_REGISTRY.keys()))

    def test_security_extra_keys_present(self) -> None:
        assert _EXPECTED_SECURITY_EXTRA_KEYS.issubset(set(SETTINGS_REGISTRY.keys()))

    def test_security_extra_keys_have_correct_category(self) -> None:
        for key in _EXPECTED_SECURITY_EXTRA_KEYS:
            assert SETTINGS_REGISTRY[key].category == "security", (
                f"{key} should have category='security'"
            )

    def test_security_extra_key_types(self) -> None:
        for key in _EXPECTED_SECURITY_EXTRA_KEYS:
            assert SETTINGS_REGISTRY[key].type == "int", f"{key} should have type='int'"

    def test_security_extra_key_defaults(self) -> None:
        expected = {
            "jwt_expiry_minutes": 60,
            "pw_reset_rate_limit_per_ip_hour": 5,
            "pw_reset_rate_limit_per_email_hour": 3,
            "email_change_rate_limit_per_user_hour": 3,
            "password_reset_ttl_minutes": 15,
            "email_change_ttl_minutes": 30,
        }
        for key, default in expected.items():
            assert SETTINGS_REGISTRY[key].default == default, (
                f"{key}: expected default {default!r}, got {SETTINGS_REGISTRY[key].default!r}"
            )

    def test_no_duplicate_keys(self) -> None:
        keys = list(SETTINGS_REGISTRY.keys())
        assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------
# step_executor: partition_size fallback when LoadStep.partition_size is None
# ---------------------------------------------------------------------------


class TestPartitionSizeFallback:
    """Verify that step_executor falls back to default_partition_size when
    LoadStep.partition_size is None."""

    @pytest.mark.asyncio
    async def test_uses_default_partition_size_when_step_partition_size_is_none(self) -> None:
        """When step.partition_size is None, the executor reads default_partition_size
        from the settings service."""
        import asyncio

        from app.models.load_step import Operation
        from app.services import step_executor

        # Build a minimal mock step with partition_size=None
        mock_step = MagicMock()
        mock_step.partition_size = None
        mock_step.operation = Operation.insert  # not a query op
        mock_step.csv_file_pattern = "test_*.csv"
        mock_step.id = "step-1"
        mock_step.object_name = "Account"
        mock_step.sequence = 1
        mock_step.input_from_step_id = None

        # Mock storage that returns one file with 2 data rows
        csv_content = "Name,Id\nFoo,1\nBar,2\n"
        mock_storage = MagicMock()
        mock_storage.provider = "local"
        mock_storage.discover_files.return_value = ["test_accounts.csv"]
        mock_storage.open_text.return_value.__enter__ = lambda s: io.StringIO(csv_content)
        mock_storage.open_text.return_value.__exit__ = MagicMock(return_value=False)

        # Mock settings service returning default_partition_size=500
        mock_svc = AsyncMock()
        mock_svc.get = AsyncMock(return_value=500)

        # Mock _get_storage and _partition; _process is a no-op returning (2, 0)
        calls: list[int] = []

        def _fake_partition(fh, partition_size: int):
            calls.append(partition_size)
            # Yield one chunk
            yield b"Name,Id\nFoo,1\nBar,2\n"

        async def _fake_process(**kwargs):
            return (2, 0)

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        mock_semaphore = asyncio.Semaphore(10)

        with (
            patch("app.services.settings.service.settings_service", mock_svc),
        ):
            result = await step_executor._execute_step(
                run_id="run-1",
                step=mock_step,
                plan=MagicMock(),
                plan_id="plan-1",
                plan_name="Test",
                bulk_client=MagicMock(),
                db=mock_db,
                semaphore=mock_semaphore,
                db_factory=MagicMock(),
                output_storage=MagicMock(),
                _get_storage=AsyncMock(return_value=mock_storage),
                _partition=_fake_partition,
                _process=_fake_process,
                _run_bulk_query=MagicMock(),
            )

        # The partition was called with 500 (from DB settings), not the model default
        assert calls == [500], f"Expected partition_size=500, got {calls}"

    @pytest.mark.asyncio
    async def test_uses_step_partition_size_when_set(self) -> None:
        """When step.partition_size is set, the executor uses it directly."""
        import asyncio

        from app.models.load_step import Operation
        from app.services import step_executor

        mock_step = MagicMock()
        mock_step.partition_size = 250  # explicit
        mock_step.operation = Operation.insert
        mock_step.csv_file_pattern = "test_*.csv"
        mock_step.id = "step-1"
        mock_step.object_name = "Account"
        mock_step.sequence = 1
        mock_step.input_from_step_id = None

        csv_content = "Name,Id\nFoo,1\n"
        mock_storage = MagicMock()
        mock_storage.provider = "local"
        mock_storage.discover_files.return_value = ["test.csv"]
        mock_storage.open_text.return_value.__enter__ = lambda s: io.StringIO(csv_content)
        mock_storage.open_text.return_value.__exit__ = MagicMock(return_value=False)

        mock_svc = AsyncMock()

        calls: list[int] = []

        def _fake_partition(fh, partition_size: int):
            calls.append(partition_size)
            yield b"Name,Id\nFoo,1\n"

        async def _fake_process(**kwargs):
            return (1, 0)

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        mock_semaphore = asyncio.Semaphore(10)

        with (
            patch("app.services.settings.service.settings_service", mock_svc),
        ):
            result = await step_executor._execute_step(
                run_id="run-1",
                step=mock_step,
                plan=MagicMock(),
                plan_id="plan-1",
                plan_name="Test",
                bulk_client=MagicMock(),
                db=mock_db,
                semaphore=mock_semaphore,
                db_factory=MagicMock(),
                output_storage=MagicMock(),
                _get_storage=AsyncMock(return_value=mock_storage),
                _partition=_fake_partition,
                _process=_fake_process,
                _run_bulk_query=MagicMock(),
            )

        assert calls == [250], f"Expected partition_size=250, got {calls}"
        # settings_service.get should NOT have been called for default_partition_size
        mock_svc.get.assert_not_called()
