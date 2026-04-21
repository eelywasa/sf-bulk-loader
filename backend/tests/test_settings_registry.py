"""Tests for the SETTINGS_REGISTRY (SFBL-153)."""

from app.services.settings.registry import SETTINGS_REGISTRY, SettingMeta

# Expected security keys seeded in Wave S1
_EXPECTED_SECURITY_KEYS = {
    "login_rate_limit_attempts",
    "login_rate_limit_window_seconds",
    "login_tier1_threshold",
    "login_tier1_window_minutes",
    "login_tier1_lock_minutes",
    "login_tier2_threshold",
    "login_tier2_tier1_count",
    "login_tier2_window_hours",
}


def test_registry_contains_all_security_keys() -> None:
    assert _EXPECTED_SECURITY_KEYS.issubset(set(SETTINGS_REGISTRY.keys()))


def test_no_duplicate_keys() -> None:
    # Registry is built with duplicate detection — just assert all keys are unique.
    keys = list(SETTINGS_REGISTRY.keys())
    assert len(keys) == len(set(keys))


def test_all_entries_are_setting_meta() -> None:
    for key, meta in SETTINGS_REGISTRY.items():
        assert isinstance(meta, SettingMeta), f"{key} is not a SettingMeta"


def test_security_keys_have_correct_category() -> None:
    for key in _EXPECTED_SECURITY_KEYS:
        assert SETTINGS_REGISTRY[key].category == "security", (
            f"{key} should have category='security'"
        )


def test_security_keys_are_int_type() -> None:
    for key in _EXPECTED_SECURITY_KEYS:
        assert SETTINGS_REGISTRY[key].type == "int", f"{key} should have type='int'"


def test_security_keys_are_not_secret() -> None:
    for key in _EXPECTED_SECURITY_KEYS:
        assert not SETTINGS_REGISTRY[key].is_secret, f"{key} should not be is_secret"


def test_security_keys_have_env_vars() -> None:
    for key in _EXPECTED_SECURITY_KEYS:
        meta = SETTINGS_REGISTRY[key]
        assert meta.env_var, f"{key} should have an env_var"


def test_security_key_defaults() -> None:
    expected_defaults = {
        "login_rate_limit_attempts": 20,
        "login_rate_limit_window_seconds": 300,
        "login_tier1_threshold": 5,
        "login_tier1_window_minutes": 15,
        "login_tier1_lock_minutes": 15,
        "login_tier2_threshold": 15,
        "login_tier2_tier1_count": 3,
        "login_tier2_window_hours": 24,
    }
    for key, expected in expected_defaults.items():
        assert SETTINGS_REGISTRY[key].default == expected, (
            f"{key}: expected default {expected}, got {SETTINGS_REGISTRY[key].default}"
        )


def test_all_meta_fields_populated() -> None:
    """Every SettingMeta must have key, category, type, and a non-None default."""
    for key, meta in SETTINGS_REGISTRY.items():
        assert meta.key, f"{key} has empty key"
        assert meta.category, f"{key} has empty category"
        assert meta.type in ("str", "int", "bool", "float"), f"{key} has invalid type {meta.type!r}"
        assert meta.default is not None, f"{key} has None default"
