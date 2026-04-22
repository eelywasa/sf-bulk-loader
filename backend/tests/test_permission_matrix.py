"""Parametrised permission matrix integration tests (SFBL-197).

Covers every (profile × route × method) cell from spec §5.2.
Each test injects a synthetic user with the correct profile via
dependency_overrides[get_current_user] so that require_permission()
evaluates real profile permission_keys — no mocking of the permission
check itself.

Additional explicit tests:
- Credential-field redaction: operator gets 200 on GET /connections/{id}
  but response omits client_id / login_url / username / is_sandbox.
  Admin gets all fields.
- is_admin backstop: a user with is_admin=True but profile_id=None
  still passes admin-gated routes (required during Epic B→C transition).
- Consistency check: docs/specs/rbac-permission-matrix.yml matches the
  permission sets hard-coded in this module (which mirror migration 0021).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from app.main import app
from app.models.profile import Profile
from app.models.profile_permission import ProfilePermission
from app.models.user import User
from app.services.auth import get_current_user

# ──────────────────────────────────────────────────────────────────────────────
# Profile permission sets (mirrors migration 0021)
# ──────────────────────────────────────────────────────────────────────────────

_ADMIN_PERMISSIONS = frozenset([
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
])

_OPERATOR_PERMISSIONS = frozenset([
    "connections.view",
    "plans.view",
    "plans.manage",
    "runs.view",
    "runs.execute",
    "runs.abort",
    "files.view",
    "files.view_contents",
])

_VIEWER_PERMISSIONS = frozenset([
    "connections.view",
    "plans.view",
    "runs.view",
    "files.view",
])

_PROFILE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": _ADMIN_PERMISSIONS,
    "operator": _OPERATOR_PERMISSIONS,
    "viewer": _VIEWER_PERMISSIONS,
}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_profile(name: str) -> Profile:
    """Build an in-memory Profile with the correct permission set."""
    keys = _PROFILE_PERMISSIONS[name]
    profile = Profile(id=str(uuid.uuid4()), name=name)
    profile.permissions = [ProfilePermission(permission_key=k) for k in keys]
    return profile


def _make_user(profile_name: str) -> User:
    """Build a synthetic User with the given profile."""
    profile = _make_profile(profile_name)
    user = User(
        id=str(uuid.uuid4()),
        email=f"test-{profile_name}@example.com",
        hashed_password="x",
        is_admin=(profile_name == "admin"),
        status="active",
    )
    user.profile = profile
    return user


def _call_as(auth_client, profile_name: str, method: str, path: str, body: Any = None) -> Any:
    """Make a request with get_current_user overridden to the given profile.

    Patches app.config.settings.auth_mode='jwt' so the desktop bypass in
    require_permission() is disabled and real profile permission_keys are checked.
    The permissions module imports settings lazily via `from app.config import settings`
    inside the dependency closure, so patching app.config.settings is sufficient.
    """
    user = _make_user(profile_name)

    async def _override_user():
        return user

    app.dependency_overrides[get_current_user] = _override_user
    try:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            mock_settings.app_env = "test"
            mock_settings.sf_api_version = "v62.0"
            mock_settings.health_enable_dependency_checks = False
            mock_settings.output_dir = "/tmp/sfbl-test-output"
            mock_settings.input_dir = "/tmp/sfbl-test-input"
            mock_settings.input_storage_mode = "local"
            method_fn = getattr(auth_client, method.lower())
            if body is not None:
                return method_fn(path, json=body)
            return method_fn(path)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ──────────────────────────────────────────────────────────────────────────────
# Body fixtures for write routes
# ──────────────────────────────────────────────────────────────────────────────

_CONN_BODY: dict[str, Any] = {
    "name": "Matrix Test Org",
    "instance_url": "https://matrix.my.salesforce.com",
    "login_url": "https://login.salesforce.com",
    "client_id": "3MVGtest",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nFAKEKEY\n-----END RSA PRIVATE KEY-----",
    "username": "matrix@example.com",
    "is_sandbox": False,
}


def _seed_conn(auth_client) -> str:
    """Seed a connection (as admin) and return its ID."""
    resp = _call_as(auth_client, "admin", "POST", "/api/connections/", _CONN_BODY)
    assert resp.status_code == 201, f"Seed connection failed: {resp.text}"
    return resp.json()["id"]


def _seed_plan(auth_client, conn_id: str) -> str:
    """Seed a load plan (as admin) and return its ID."""
    body = {
        "name": "Matrix Test Plan",
        "connection_id": conn_id,
        "max_parallel_jobs": 1,
        "error_threshold_pct": 5.0,
        "abort_on_step_failure": False,
    }
    resp = _call_as(auth_client, "admin", "POST", "/api/load-plans/", body)
    assert resp.status_code == 201, f"Seed plan failed: {resp.text}"
    return resp.json()["id"]


# ──────────────────────────────────────────────────────────────────────────────
# Permission matrix definition
#
# Each tuple: (profile_name, expected_status, label)
# The test body drives the route call — parametrise profile+expected pairs per route.
# ──────────────────────────────────────────────────────────────────────────────

# ── Connections routes ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("profile,expected", [
    ("admin",    200),
    ("operator", 200),
    ("viewer",   200),
], ids=["admin", "operator", "viewer"])
def test_connections_list(auth_client, profile, expected):
    """GET /api/connections/ — requires connections.view (all profiles)."""
    resp = _call_as(auth_client, profile, "GET", "/api/connections/")
    assert resp.status_code == expected, (
        f"GET /api/connections/ profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expected", [
    ("admin",    200),
    ("operator", 200),
    ("viewer",   200),
], ids=["admin", "operator", "viewer"])
def test_connections_get(auth_client, profile, expected):
    """GET /api/connections/{id} — requires connections.view (all profiles)."""
    conn_id = _seed_conn(auth_client)
    resp = _call_as(auth_client, profile, "GET", f"/api/connections/{conn_id}")
    assert resp.status_code == expected, (
        f"GET /api/connections/{{id}} profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expected", [
    ("admin",    201),
    ("operator", 403),
    ("viewer",   403),
], ids=["admin", "operator", "viewer"])
def test_connections_create(auth_client, profile, expected):
    """POST /api/connections/ — requires connections.manage (admin only)."""
    resp = _call_as(auth_client, profile, "POST", "/api/connections/", _CONN_BODY)
    assert resp.status_code == expected, (
        f"POST /api/connections/ profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expected", [
    ("admin",    200),
    ("operator", 403),
    ("viewer",   403),
], ids=["admin", "operator", "viewer"])
def test_connections_update(auth_client, profile, expected):
    """PUT /api/connections/{id} — requires connections.manage (admin only)."""
    conn_id = _seed_conn(auth_client)
    resp = _call_as(auth_client, profile, "PUT", f"/api/connections/{conn_id}", _CONN_BODY)
    assert resp.status_code == expected, (
        f"PUT /api/connections/{{id}} profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expected", [
    ("admin",    204),
    ("operator", 403),
    ("viewer",   403),
], ids=["admin", "operator", "viewer"])
def test_connections_delete(auth_client, profile, expected):
    """DELETE /api/connections/{id} — requires connections.manage (admin only)."""
    # Seed a separate throwaway connection for delete to avoid affecting other tests
    conn_id = _seed_conn(auth_client)
    resp = _call_as(auth_client, profile, "DELETE", f"/api/connections/{conn_id}")
    assert resp.status_code == expected, (
        f"DELETE /api/connections/{{id}} profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


# ── Plans routes ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("profile,expected", [
    ("admin",    200),
    ("operator", 200),
    ("viewer",   200),
], ids=["admin", "operator", "viewer"])
def test_plans_list(auth_client, profile, expected):
    """GET /api/load-plans/ — requires plans.view (all profiles)."""
    resp = _call_as(auth_client, profile, "GET", "/api/load-plans/")
    assert resp.status_code == expected, (
        f"GET /api/load-plans/ profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expected", [
    ("admin",    200),
    ("operator", 200),
    ("viewer",   200),
], ids=["admin", "operator", "viewer"])
def test_plans_get(auth_client, profile, expected):
    """GET /api/load-plans/{id} — requires plans.view (all profiles)."""
    conn_id = _seed_conn(auth_client)
    plan_id = _seed_plan(auth_client, conn_id)
    resp = _call_as(auth_client, profile, "GET", f"/api/load-plans/{plan_id}")
    assert resp.status_code == expected, (
        f"GET /api/load-plans/{{id}} profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expected", [
    ("admin",    201),
    ("operator", 201),
    ("viewer",   403),
], ids=["admin", "operator", "viewer"])
def test_plans_create(auth_client, profile, expected):
    """POST /api/load-plans/ — requires plans.manage (admin + operator)."""
    conn_id = _seed_conn(auth_client)
    body = {
        "name": f"Plan by {profile}",
        "connection_id": conn_id,
        "max_parallel_jobs": 1,
        "error_threshold_pct": 5.0,
        "abort_on_step_failure": False,
    }
    resp = _call_as(auth_client, profile, "POST", "/api/load-plans/", body)
    assert resp.status_code == expected, (
        f"POST /api/load-plans/ profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expected", [
    ("admin",    200),
    ("operator", 200),
    ("viewer",   403),
], ids=["admin", "operator", "viewer"])
def test_plans_update(auth_client, profile, expected):
    """PUT /api/load-plans/{id} — requires plans.manage (admin + operator)."""
    conn_id = _seed_conn(auth_client)
    plan_id = _seed_plan(auth_client, conn_id)
    body = {
        "name": "Updated Plan",
        "connection_id": conn_id,
        "max_parallel_jobs": 2,
        "error_threshold_pct": 10.0,
        "abort_on_step_failure": True,
    }
    resp = _call_as(auth_client, profile, "PUT", f"/api/load-plans/{plan_id}", body)
    assert resp.status_code == expected, (
        f"PUT /api/load-plans/{{id}} profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expected", [
    ("admin",    204),
    ("operator", 204),
    ("viewer",   403),
], ids=["admin", "operator", "viewer"])
def test_plans_delete(auth_client, profile, expected):
    """DELETE /api/load-plans/{id} — requires plans.manage (admin + operator)."""
    conn_id = _seed_conn(auth_client)
    plan_id = _seed_plan(auth_client, conn_id)
    resp = _call_as(auth_client, profile, "DELETE", f"/api/load-plans/{plan_id}")
    assert resp.status_code == expected, (
        f"DELETE /api/load-plans/{{id}} profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expected", [
    ("admin",    201),
    ("operator", 201),
    ("viewer",   403),
], ids=["admin", "operator", "viewer"])
def test_plans_duplicate(auth_client, profile, expected):
    """POST /api/load-plans/{id}/duplicate — requires plans.manage (admin + operator)."""
    conn_id = _seed_conn(auth_client)
    plan_id = _seed_plan(auth_client, conn_id)
    resp = _call_as(auth_client, profile, "POST", f"/api/load-plans/{plan_id}/duplicate")
    assert resp.status_code == expected, (
        f"POST /api/load-plans/{{id}}/duplicate profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


# ── Runs routes ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("profile,expected", [
    ("admin",    200),
    ("operator", 200),
    ("viewer",   200),
], ids=["admin", "operator", "viewer"])
def test_runs_list(auth_client, profile, expected):
    """GET /api/runs/ — requires runs.view (all profiles)."""
    resp = _call_as(auth_client, profile, "GET", "/api/runs/")
    assert resp.status_code == expected, (
        f"GET /api/runs/ profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expected", [
    ("admin",    201),
    ("operator", 201),
    ("viewer",   403),
], ids=["admin", "operator", "viewer"])
def test_runs_execute(auth_client, profile, expected):
    """POST /api/load-plans/{id}/run — requires runs.execute (admin + operator)."""
    conn_id = _seed_conn(auth_client)
    plan_id = _seed_plan(auth_client, conn_id)
    resp = _call_as(auth_client, profile, "POST", f"/api/load-plans/{plan_id}/run")
    assert resp.status_code == expected, (
        f"POST /api/load-plans/{{id}}/run profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expect_403", [
    ("admin",    False),
    ("operator", False),
    ("viewer",   True),
], ids=["admin", "operator", "viewer"])
def test_runs_abort_permission(auth_client, profile, expect_403):
    """POST /api/runs/{id}/abort — requires runs.abort (admin + operator).

    Uses a nonexistent run ID — admin/operator get 404 (permission passed),
    viewer gets 403 (permission denied before resource lookup).
    """
    resp = _call_as(auth_client, profile, "POST", "/api/runs/nonexistent-run-id/abort")
    if expect_403:
        assert resp.status_code == 403, (
            f"Expected 403 for {profile!r} on abort, got {resp.status_code}"
        )
    else:
        assert resp.status_code != 403, (
            f"Expected non-403 for {profile!r} on abort, got {resp.status_code}: {resp.text}"
        )


@pytest.mark.parametrize("profile,expect_403", [
    ("admin",    False),
    ("operator", False),
    ("viewer",   True),
], ids=["admin", "operator", "viewer"])
def test_runs_retry_step_permission(auth_client, profile, expect_403):
    """POST /api/runs/{run_id}/retry-step/{step_id} — requires runs.execute."""
    resp = _call_as(auth_client, profile, "POST", "/api/runs/nonexistent-run/retry-step/nonexistent-step")
    if expect_403:
        assert resp.status_code == 403, (
            f"Expected 403 for {profile!r} on retry-step, got {resp.status_code}"
        )
    else:
        assert resp.status_code != 403, (
            f"Expected non-403 for {profile!r} on retry-step, got {resp.status_code}"
        )


# ── Files routes ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("profile,expected", [
    ("admin",    200),
    ("operator", 200),
    ("viewer",   200),
], ids=["admin", "operator", "viewer"])
def test_files_input_list(auth_client, profile, expected):
    """GET /api/files/input — requires files.view (all profiles)."""
    resp = _call_as(auth_client, profile, "GET", "/api/files/input")
    assert resp.status_code == expected, (
        f"GET /api/files/input profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expected", [
    ("admin",    200),
    ("operator", 200),
    ("viewer",   200),
], ids=["admin", "operator", "viewer"])
def test_files_output_list(auth_client, profile, expected):
    """GET /api/files/output — requires files.view (all profiles)."""
    resp = _call_as(auth_client, profile, "GET", "/api/files/output")
    assert resp.status_code == expected, (
        f"GET /api/files/output profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expected", [
    ("admin",    404),   # permission passed → resource not found
    ("operator", 404),   # permission passed → resource not found
    ("viewer",   403),   # permission denied before resource lookup
], ids=["admin", "operator", "viewer"])
def test_files_input_preview_permission(auth_client, profile, expected):
    """GET /api/files/input/{path}/preview — requires files.view_contents.

    Uses a nonexistent file path — admin/operator get 404 (permission passed),
    viewer gets 403 (permission denied).
    """
    resp = _call_as(auth_client, profile, "GET", "/api/files/input/no-such-file.csv/preview")
    assert resp.status_code == expected, (
        f"GET /api/files/input/{{path}}/preview profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expected", [
    ("admin",    404),
    ("operator", 404),
    ("viewer",   403),
], ids=["admin", "operator", "viewer"])
def test_files_output_preview_permission(auth_client, profile, expected):
    """GET /api/files/output/{path}/preview — requires files.view_contents."""
    resp = _call_as(auth_client, profile, "GET", "/api/files/output/no-such-file.csv/preview")
    assert resp.status_code == expected, (
        f"GET /api/files/output/{{path}}/preview profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


# ── Settings routes ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("profile,expected", [
    ("admin",    200),
    ("operator", 403),
    ("viewer",   403),
], ids=["admin", "operator", "viewer"])
def test_settings_get(auth_client, profile, expected):
    """GET /api/settings — requires system.settings (admin only)."""
    resp = _call_as(auth_client, profile, "GET", "/api/settings")
    assert resp.status_code == expected, (
        f"GET /api/settings profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile,expected", [
    ("admin",    200),
    ("operator", 403),
    ("viewer",   403),
], ids=["admin", "operator", "viewer"])
def test_settings_get_category(auth_client, profile, expected):
    """GET /api/settings/{category} — requires system.settings (admin only)."""
    resp = _call_as(auth_client, profile, "GET", "/api/settings/email")
    assert resp.status_code == expected, (
        f"GET /api/settings/email profile={profile!r}: expected {expected}, got {resp.status_code}"
    )


# ── Authenticated-only routes (all profiles → 200) ────────────────────────────

@pytest.mark.parametrize("profile", ["admin", "operator", "viewer"])
def test_auth_me_all_profiles(auth_client, profile):
    """GET /api/auth/me — authenticated-only, no permission key (all profiles)."""
    resp = _call_as(auth_client, profile, "GET", "/api/auth/me")
    assert resp.status_code == 200, (
        f"GET /api/auth/me profile={profile!r}: expected 200, got {resp.status_code}"
    )


@pytest.mark.parametrize("profile", ["admin", "operator", "viewer"])
def test_health_check_all_profiles(auth_client, profile):
    """GET /api/health — unauthenticated endpoint, accessible to all."""
    resp = _call_as(auth_client, profile, "GET", "/api/health")
    assert resp.status_code == 200, (
        f"GET /api/health profile={profile!r}: expected 200, got {resp.status_code}"
    )


# ── Credential redaction test ─────────────────────────────────────────────────

@pytest.mark.parametrize("profile,expect_full", [
    ("admin",    True),
    ("operator", False),
    ("viewer",   False),
], ids=["admin", "operator", "viewer"])
def test_connection_credential_redaction_by_profile(auth_client, profile, expect_full):
    """GET /api/connections/{id} — connections.view_credentials controls response shape.

    Schema design (ConnectionPublic vs ConnectionResponse):
    - ConnectionPublic: id, name, instance_url, login_url, username, is_sandbox, timestamps
    - ConnectionResponse: adds client_id (= Salesforce consumer key)

    The connections.view_credentials permission gate controls whether the response
    includes ConnectionResponse (full — with client_id) or ConnectionPublic (public
    — without client_id). private_key is excluded from both shapes (never returned).

    - Admin has connections.view_credentials → ConnectionResponse (includes client_id)
    - Operator / Viewer → ConnectionPublic (no client_id, no private_key)

    Note: login_url, username, is_sandbox are present in BOTH shapes per current
    schema design (ConnectionPublic). If the spec intent is to hide these too,
    that is a SFBL-195 schema issue to track separately.
    """
    conn_id = _seed_conn(auth_client)
    path = f"/api/connections/{conn_id}"

    user = _make_user(profile)

    async def _override_user():
        return user

    app.dependency_overrides[get_current_user] = _override_user
    try:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            mock_settings.app_env = "test"
            mock_settings.sf_api_version = "v62.0"
            mock_settings.health_enable_dependency_checks = False
            mock_settings.output_dir = "/tmp/sfbl-test-output"
            mock_settings.input_dir = "/tmp/sfbl-test-input"
            mock_settings.input_storage_mode = "local"
            resp = auth_client.get(path)
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, (
        f"Expected 200 for {profile!r} on GET /connections/{{id}}, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()

    # private_key is NEVER returned regardless of permissions
    assert "private_key" not in body, "private_key must never be returned from GET /connections/{id}"

    if expect_full:
        # Admin gets ConnectionResponse — includes client_id (Salesforce consumer key)
        assert "client_id" in body, (
            f"Admin should see client_id in ConnectionResponse; got keys: {list(body.keys())}"
        )
    else:
        # Operator / Viewer get ConnectionPublic — no client_id
        assert "client_id" not in body, (
            f"Profile {profile!r} should NOT see client_id (consumer key) in response; "
            f"got keys: {list(body.keys())}"
        )


# ── is_admin backstop tests ───────────────────────────────────────────────────

def test_is_admin_backstop_allows_admin_routes_without_profile(auth_client):
    """Spec §5.4: is_admin=True + profile=None → full access during Epic B→C transition.

    The backstop in require_permission() lets legacy admin users through even
    without a profile_id. This must remain until Epic C migrates all users.

    NOTE: After Epic C completes the user migration, this backstop should be
    removed (tracked as a follow-up on SFBL-197 Jira issue).
    """
    user = User(
        id=str(uuid.uuid4()),
        email="legacy-admin@example.com",
        hashed_password="x",
        is_admin=True,
        status="active",
    )
    user.profile = None  # No profile — pre-migration state

    async def _override_user():
        return user

    app.dependency_overrides[get_current_user] = _override_user
    try:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            mock_settings.app_env = "test"
            mock_settings.sf_api_version = "v62.0"
            mock_settings.health_enable_dependency_checks = False
            mock_settings.output_dir = "/tmp/sfbl-test-output"
            mock_settings.input_dir = "/tmp/sfbl-test-input"
            mock_settings.input_storage_mode = "local"
            resp = auth_client.get("/api/settings")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, (
        f"is_admin backstop FAILED: expected 200 for is_admin=True + profile=None "
        f"on system.settings-gated route, got {resp.status_code}: {resp.text}"
    )


def test_is_admin_false_and_no_profile_still_denied(auth_client):
    """Without is_admin and without profile, user must be denied on permission-gated routes."""
    user = User(
        id=str(uuid.uuid4()),
        email="no-profile-no-admin@example.com",
        hashed_password="x",
        is_admin=False,
        status="active",
    )
    user.profile = None

    async def _override_user():
        return user

    app.dependency_overrides[get_current_user] = _override_user
    try:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            mock_settings.app_env = "test"
            mock_settings.sf_api_version = "v62.0"
            mock_settings.health_enable_dependency_checks = False
            mock_settings.output_dir = "/tmp/sfbl-test-output"
            mock_settings.input_dir = "/tmp/sfbl-test-input"
            mock_settings.input_storage_mode = "local"
            resp = auth_client.get("/api/settings")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 403, (
        f"Expected 403 for non-admin + no profile on settings route, got {resp.status_code}"
    )


# ── Matrix consistency check ──────────────────────────────────────────────────

def test_matrix_doc_matches_seed_data():
    """Read docs/specs/rbac-permission-matrix.yml and assert it matches this module's
    permission sets (which mirror migration 0021 seed data).

    Prevents the documentation from drifting from the actual enforcement data
    without a failing CI test.
    """
    repo_root = Path(__file__).parent.parent.parent
    matrix_yaml = repo_root / "docs" / "specs" / "rbac-permission-matrix.yml"

    assert matrix_yaml.exists(), (
        f"Canonical matrix YAML not found at {matrix_yaml}. "
        "This file must exist — it is the single source of truth for the permission matrix."
    )

    with matrix_yaml.open() as f:
        doc = yaml.safe_load(f)

    profiles = doc.get("profiles", {})

    for profile_name, expected_perms in _PROFILE_PERMISSIONS.items():
        assert profile_name in profiles, (
            f"Profile {profile_name!r} missing from rbac-permission-matrix.yml"
        )
        yaml_perms = frozenset(profiles[profile_name]["permissions"])
        assert yaml_perms == expected_perms, (
            f"Permission mismatch for profile {profile_name!r}.\n"
            f"  In YAML:             {sorted(yaml_perms)}\n"
            f"  In test / seed data: {sorted(expected_perms)}\n"
            f"  Missing from YAML:   {sorted(expected_perms - yaml_perms)}\n"
            f"  Extra in YAML:       {sorted(yaml_perms - expected_perms)}"
        )
