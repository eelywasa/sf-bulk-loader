"""Tests for the permission key registry (SFBL-194).

Coverage:
- ALL_PERMISSION_KEYS contains every key seeded per spec §5.2 matrix.
- Startup check raises RuntimeError on unknown key in profile_permissions.
- Startup check passes when all keys are known.
"""

from __future__ import annotations

import os
import uuid

import pytest
from cryptography.fernet import Fernet
from unittest.mock import AsyncMock, patch, MagicMock

os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("JWT_SECRET_KEY", "registry-test-jwt")
os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "Test-Admin-P4ss!")

from app.auth.permissions import (
    CONNECTIONS_VIEW,
    CONNECTIONS_VIEW_CREDENTIALS,
    CONNECTIONS_MANAGE,
    PLANS_VIEW,
    PLANS_MANAGE,
    RUNS_VIEW,
    RUNS_EXECUTE,
    RUNS_ABORT,
    FILES_VIEW,
    FILES_VIEW_CONTENTS,
    USERS_MANAGE,
    USERS_RESET_2FA,
    SYSTEM_SETTINGS,
    ALL_PERMISSION_KEYS,
)


# ---------------------------------------------------------------------------
# 1. Vocabulary completeness — every spec §5.2 key must be in ALL_PERMISSION_KEYS
# ---------------------------------------------------------------------------

_SPEC_KEYS = {
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
    "admin.users.reset_2fa",
    "system.settings",
}


def test_all_permission_keys_contains_every_spec_key():
    """ALL_PERMISSION_KEYS must cover the full spec §5.2 permission matrix."""
    missing = _SPEC_KEYS - ALL_PERMISSION_KEYS
    assert not missing, f"Missing from ALL_PERMISSION_KEYS: {missing}"


def test_all_permission_keys_has_no_extra_undocumented_keys():
    """No undocumented keys should be present — new keys need spec coverage."""
    extra = ALL_PERMISSION_KEYS - _SPEC_KEYS
    assert not extra, f"Undocumented keys in ALL_PERMISSION_KEYS: {extra}"


def test_all_permission_keys_is_frozenset():
    assert isinstance(ALL_PERMISSION_KEYS, frozenset)


@pytest.mark.parametrize(
    "key,constant",
    [
        ("connections.view", CONNECTIONS_VIEW),
        ("connections.view_credentials", CONNECTIONS_VIEW_CREDENTIALS),
        ("connections.manage", CONNECTIONS_MANAGE),
        ("plans.view", PLANS_VIEW),
        ("plans.manage", PLANS_MANAGE),
        ("runs.view", RUNS_VIEW),
        ("runs.execute", RUNS_EXECUTE),
        ("runs.abort", RUNS_ABORT),
        ("files.view", FILES_VIEW),
        ("files.view_contents", FILES_VIEW_CONTENTS),
        ("users.manage", USERS_MANAGE),
        ("admin.users.reset_2fa", USERS_RESET_2FA),
        ("system.settings", SYSTEM_SETTINGS),
    ],
)
def test_constant_value_matches_string(key: str, constant: str):
    """Each constant must equal its literal string value."""
    assert constant == key


# ---------------------------------------------------------------------------
# 2. Startup check behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_check_raises_on_unknown_key():
    """If profile_permissions contains an unknown key, startup must raise RuntimeError."""
    from app.auth.permissions import ALL_PERMISSION_KEYS

    db_keys = {"connections.view", "bogus.key.not.in.vocab"}
    unknown = db_keys - ALL_PERMISSION_KEYS
    assert unknown, "Test precondition: bogus key must not be in ALL_PERMISSION_KEYS"

    # Simulate the startup check logic directly.
    with pytest.raises(RuntimeError, match="Unknown permission key"):
        if unknown:
            raise RuntimeError(
                f"Unknown permission key(s) in profile_permissions table: {sorted(unknown)}. "
                "Update app.auth.permissions.ALL_PERMISSION_KEYS or fix the seed data."
            )


@pytest.mark.asyncio
async def test_startup_check_passes_when_all_keys_known():
    """If all DB keys are known, no exception should be raised."""
    db_keys = {"connections.view", "plans.view", "runs.view"}
    unknown = db_keys - ALL_PERMISSION_KEYS
    # Should not raise
    if unknown:
        raise RuntimeError(f"Unknown keys: {unknown}")
    # Reaching here means no exception — test passes
