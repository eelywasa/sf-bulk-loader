"""Tests for SettingsService (SFBL-153).

Uses the shared test database (file-based SQLite, created by conftest) so
tables are guaranteed to exist.  Each test gets a clean app_settings table
via the _clean_app_settings fixture.
"""

import time

import pytest
from sqlalchemy import delete

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


# ---------------------------------------------------------------------------
# Helper: temporarily add a key to the registry for the duration of a test
# ---------------------------------------------------------------------------


def _add_temp_key(meta: SettingMeta) -> None:
    SETTINGS_REGISTRY[meta.key] = meta


def _remove_temp_key(key: str) -> None:
    SETTINGS_REGISTRY.pop(key, None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_returns_default_when_db_empty() -> None:
    svc = _make_service()
    # login_tier1_threshold has default 5
    result = _run_async(svc.get("login_tier1_threshold"))
    assert result == 5
    assert isinstance(result, int)


def test_set_and_get_roundtrip_int() -> None:
    svc = _make_service()
    _run_async(svc.set("login_tier1_threshold", 99))
    result = _run_async(svc.get("login_tier1_threshold"))
    assert result == 99
    assert isinstance(result, int)


def test_set_and_get_roundtrip_str() -> None:
    meta = SettingMeta(key="_test_str_key", category="security", type="str", default="hello")
    _add_temp_key(meta)
    try:
        svc = _make_service()
        _run_async(svc.set("_test_str_key", "world"))
        result = _run_async(svc.get("_test_str_key"))
        assert result == "world"
        assert isinstance(result, str)
    finally:
        _remove_temp_key("_test_str_key")


def test_set_and_get_roundtrip_bool() -> None:
    meta = SettingMeta(key="_test_bool_key", category="security", type="bool", default=False)
    _add_temp_key(meta)
    try:
        svc = _make_service()
        _run_async(svc.set("_test_bool_key", True))
        result = _run_async(svc.get("_test_bool_key"))
        assert result is True
    finally:
        _remove_temp_key("_test_bool_key")


def test_set_and_get_roundtrip_float() -> None:
    meta = SettingMeta(key="_test_float_key", category="security", type="float", default=1.5)
    _add_temp_key(meta)
    try:
        svc = _make_service()
        _run_async(svc.set("_test_float_key", 3.14))
        result = _run_async(svc.get("_test_float_key"))
        assert abs(result - 3.14) < 1e-9
        assert isinstance(result, float)
    finally:
        _remove_temp_key("_test_float_key")


def test_secret_encrypted_at_rest() -> None:
    """is_secret=True: raw DB value is ciphertext; get() returns plaintext."""
    meta = SettingMeta(
        key="_test_secret_key",
        category="security",
        type="str",
        default="plaintext",
        is_secret=True,
    )
    _add_temp_key(meta)
    try:
        svc = _make_service()
        _run_async(svc.set("_test_secret_key", "my-secret-value"))

        # Read raw row from DB — must not be plaintext
        async def _read_raw() -> str | None:
            async with _TestSession() as s:
                row = await s.get(AppSetting, "_test_secret_key")
                return row.value if row else None

        raw = _run_async(_read_raw())
        assert raw is not None
        assert raw != "my-secret-value", "Secret must be stored encrypted, not as plaintext"

        # get() must return decrypted plaintext
        result = _run_async(svc.get("_test_secret_key"))
        assert result == "my-secret-value"
    finally:
        _remove_temp_key("_test_secret_key")


def test_cache_ttl_returns_cached_value() -> None:
    """A second get() within TTL should return the cached value without a DB hit."""
    svc = _make_service()
    _run_async(svc.set("login_rate_limit_attempts", 42))
    # Warm the cache
    v1 = _run_async(svc.get("login_rate_limit_attempts"))
    assert v1 == 42

    # Manually write a different value directly to DB (bypassing service) to
    # prove the service still returns the cached value.
    async def _write_raw() -> None:
        async with _TestSession() as s:
            row = await s.get(AppSetting, "login_rate_limit_attempts")
            if row:
                row.value = "99"
                await s.commit()

    _run_async(_write_raw())

    # Should still return cached 42
    v2 = _run_async(svc.get("login_rate_limit_attempts"))
    assert v2 == 42


def test_set_invalidates_cache() -> None:
    """set() must invalidate the cache so the next get() reads from DB."""
    svc = _make_service()
    _run_async(svc.set("login_rate_limit_attempts", 10))
    # Warm cache
    _run_async(svc.get("login_rate_limit_attempts"))
    # set() a new value → cache invalidated
    _run_async(svc.set("login_rate_limit_attempts", 55))
    result = _run_async(svc.get("login_rate_limit_attempts"))
    assert result == 55


def test_cache_expires_after_ttl() -> None:
    """After TTL expires the service re-reads from DB."""
    svc = _make_service()
    _run_async(svc.set("login_rate_limit_attempts", 10))
    _run_async(svc.get("login_rate_limit_attempts"))  # warm cache

    # Expire the cache entry manually
    svc._cache["login_rate_limit_attempts"] = (10, time.monotonic() - 1.0)

    # Write a different value directly to DB
    async def _write_raw() -> None:
        async with _TestSession() as s:
            row = await s.get(AppSetting, "login_rate_limit_attempts")
            if row:
                row.value = "77"
                await s.commit()

    _run_async(_write_raw())

    # Should now read from DB and return 77
    result = _run_async(svc.get("login_rate_limit_attempts"))
    assert result == 77


def test_type_coercion_string_to_int() -> None:
    """DB stores text; get() must coerce to int for int-typed keys."""
    from datetime import datetime, timezone

    async def _write_raw() -> None:
        async with _TestSession() as s:
            row = AppSetting(
                key="login_rate_limit_window_seconds",
                value="123",
                is_encrypted=False,
                category="security",
                updated_at=datetime.now(tz=timezone.utc).replace(tzinfo=None),
            )
            s.add(row)
            await s.commit()

    _run_async(_write_raw())

    svc = _make_service()
    result = _run_async(svc.get("login_rate_limit_window_seconds"))
    assert result == 123
    assert isinstance(result, int)


def test_unknown_key_raises_key_error() -> None:
    svc = _make_service()
    with pytest.raises(KeyError, match="Unknown setting key"):
        _run_async(svc.get("this_key_does_not_exist"))


def test_unknown_key_set_raises_key_error() -> None:
    svc = _make_service()
    with pytest.raises(KeyError, match="Unknown setting key"):
        _run_async(svc.set("this_key_does_not_exist", 1))
