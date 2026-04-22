"""Tests for Profile and ProfilePermission ORM models (SFBL-194).

Coverage:
- Profile.permission_keys returns a frozenset of expected strings.
- frozenset supports O(1) membership testing.
- cached_property is computed once per instance.
- User.profile relationship loads permissions.
"""

from __future__ import annotations

import os
import uuid

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("JWT_SECRET_KEY", "model-test-jwt")
os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "Test-Admin-P4ss!")

from app.auth.permissions import (
    CONNECTIONS_MANAGE,
    CONNECTIONS_VIEW,
    CONNECTIONS_VIEW_CREDENTIALS,
    FILES_VIEW,
    FILES_VIEW_CONTENTS,
    PLANS_MANAGE,
    PLANS_VIEW,
    RUNS_ABORT,
    RUNS_EXECUTE,
    RUNS_VIEW,
    SYSTEM_SETTINGS,
    USERS_MANAGE,
    ALL_PERMISSION_KEYS,
)
from app.models.profile import Profile
from app.models.profile_permission import ProfilePermission
from app.models.user import User


def _make_profile(name: str, keys: list[str]) -> Profile:
    p = Profile(id=str(uuid.uuid4()), name=name, is_system=True)
    p.permissions = [ProfilePermission(profile_id=p.id, permission_key=k) for k in keys]
    return p


def test_permission_keys_returns_frozenset():
    p = _make_profile("admin", list(ALL_PERMISSION_KEYS))
    result = p.permission_keys
    assert isinstance(result, frozenset)


def test_admin_profile_has_all_keys():
    p = _make_profile("admin", list(ALL_PERMISSION_KEYS))
    assert p.permission_keys == ALL_PERMISSION_KEYS


def test_viewer_profile_keys():
    viewer_keys = [CONNECTIONS_VIEW, PLANS_VIEW, RUNS_VIEW, FILES_VIEW]
    p = _make_profile("viewer", viewer_keys)
    keys = p.permission_keys
    assert CONNECTIONS_VIEW in keys
    assert PLANS_VIEW in keys
    assert RUNS_VIEW in keys
    assert FILES_VIEW in keys
    # Viewer does NOT have these
    assert RUNS_EXECUTE not in keys
    assert FILES_VIEW_CONTENTS not in keys
    assert USERS_MANAGE not in keys


def test_operator_profile_keys():
    operator_keys = [
        CONNECTIONS_VIEW,
        PLANS_VIEW,
        PLANS_MANAGE,
        RUNS_VIEW,
        RUNS_EXECUTE,
        RUNS_ABORT,
        FILES_VIEW,
        FILES_VIEW_CONTENTS,
    ]
    p = _make_profile("operator", operator_keys)
    keys = p.permission_keys
    assert PLANS_MANAGE in keys
    assert RUNS_EXECUTE in keys
    assert CONNECTIONS_VIEW_CREDENTIALS not in keys
    assert USERS_MANAGE not in keys
    assert SYSTEM_SETTINGS not in keys


def test_membership_check_is_o1():
    """frozenset membership is O(1); verify a simple positive and negative check."""
    p = _make_profile("admin", list(ALL_PERMISSION_KEYS))
    assert CONNECTIONS_MANAGE in p.permission_keys
    assert "not.a.real.key" not in p.permission_keys


def test_empty_profile_has_empty_keys():
    p = _make_profile("empty", [])
    assert p.permission_keys == frozenset()


def test_permission_keys_cached(monkeypatch):
    """cached_property should return the same frozenset object on repeated access."""
    p = _make_profile("admin", list(ALL_PERMISSION_KEYS))
    first = p.permission_keys
    second = p.permission_keys
    assert first is second


def test_user_role_property_admin():
    u = User(id=str(uuid.uuid4()), username="admin", is_admin=True, status="active")
    assert u.role == "admin"


def test_user_role_property_non_admin():
    u = User(id=str(uuid.uuid4()), username="viewer", is_admin=False, status="active")
    assert u.role == "user"
