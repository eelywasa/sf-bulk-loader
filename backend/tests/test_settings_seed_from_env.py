"""Tests for SettingsService.seed_from_env() (SFBL-153).

Verifies idempotency, env-var preference over registry default, and
correct handling of absent env vars.  Uses the shared test DB from conftest.
"""

import pytest
from sqlalchemy import delete, func, select

from app.models.app_setting import AppSetting
from app.services.settings.registry import SETTINGS_REGISTRY, SettingMeta
from app.services.settings.service import SettingsService
from app.utils.encryption import decrypt_secret
from tests.conftest import _TestSession, _run_async


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_app_settings():
    """Delete all app_settings rows before each test."""

    async def _clean():
        async with _TestSession() as s:
            await s.execute(delete(AppSetting))
            await s.commit()

    _run_async(_clean())
    yield
    _run_async(_clean())


def _make_service() -> SettingsService:
    return SettingsService(_TestSession)


def _add_temp_key(meta: SettingMeta) -> None:
    SETTINGS_REGISTRY[meta.key] = meta


def _remove_temp_key(key: str) -> None:
    SETTINGS_REGISTRY.pop(key, None)


async def _get_row(key: str) -> AppSetting | None:
    async with _TestSession() as s:
        return await s.get(AppSetting, key)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_seed_with_env_vars_uses_env_value(monkeypatch) -> None:
    """Env var set → DB row created with that value (not the default)."""
    monkeypatch.setenv("LOGIN_TIER1_THRESHOLD", "42")
    assert _run_async(_get_row("login_tier1_threshold")) is None

    svc = _make_service()
    _run_async(svc.seed_from_env())

    row = _run_async(_get_row("login_tier1_threshold"))
    assert row is not None
    assert row.value == "42"


def test_seed_without_env_vars_uses_default(monkeypatch) -> None:
    """Env var absent → DB row created with registry default."""
    monkeypatch.delenv("LOGIN_TIER1_THRESHOLD", raising=False)
    assert _run_async(_get_row("login_tier1_threshold")) is None

    svc = _make_service()
    _run_async(svc.seed_from_env())

    row = _run_async(_get_row("login_tier1_threshold"))
    assert row is not None
    # Default from registry is 5
    assert row.value == "5"


def test_seed_is_idempotent(monkeypatch) -> None:
    """Pre-existing DB rows must not be overwritten by a second seed call."""
    monkeypatch.setenv("LOGIN_TIER1_THRESHOLD", "99")

    svc = _make_service()
    _run_async(svc.seed_from_env())

    # Manually set a different value in the DB
    async def _overwrite() -> None:
        async with _TestSession() as s:
            row = await s.get(AppSetting, "login_tier1_threshold")
            if row:
                row.value = "77"
                await s.commit()

    _run_async(_overwrite())

    # Re-seed — should NOT overwrite
    monkeypatch.setenv("LOGIN_TIER1_THRESHOLD", "99")
    _run_async(svc.seed_from_env())

    row = _run_async(_get_row("login_tier1_threshold"))
    assert row is not None
    assert row.value == "77", "seed_from_env must not overwrite existing rows"


def test_seed_encrypts_secrets(monkeypatch) -> None:
    """is_secret=True keys must be stored encrypted during seed."""
    secret_key = "_test_seed_secret"
    meta = SettingMeta(
        key=secret_key,
        category="security",
        type="str",
        default="default-secret",
        is_secret=True,
        env_var="_TEST_SEED_SECRET",
    )
    _add_temp_key(meta)
    monkeypatch.setenv("_TEST_SEED_SECRET", "my-plaintext-secret")
    try:
        svc = _make_service()
        _run_async(svc.seed_from_env())

        row = _run_async(_get_row(secret_key))
        assert row is not None
        assert row.is_encrypted is True
        # Raw stored value must not be plaintext
        assert row.value != "my-plaintext-secret"
        # But must decrypt correctly
        assert decrypt_secret(row.value) == "my-plaintext-secret"
    finally:
        _remove_temp_key(secret_key)
        monkeypatch.delenv("_TEST_SEED_SECRET", raising=False)


def test_seed_creates_rows_for_all_registry_keys(monkeypatch) -> None:
    """After seed_from_env, every registry key must have a DB row."""
    # Scrub all relevant env vars so we only get defaults
    for meta in SETTINGS_REGISTRY.values():
        if meta.env_var:
            monkeypatch.delenv(meta.env_var, raising=False)

    svc = _make_service()
    _run_async(svc.seed_from_env())

    async def _count_rows() -> int:
        async with _TestSession() as s:
            result = await s.execute(select(func.count()).select_from(AppSetting))
            return result.scalar_one()

    count = _run_async(_count_rows())
    assert count == len(SETTINGS_REGISTRY), (
        f"Expected {len(SETTINGS_REGISTRY)} rows after seed, got {count}"
    )
