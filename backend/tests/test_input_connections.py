"""Tests for the /api/input-connections endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.main import app
from app.models.profile import Profile
from app.models.profile_permission import ProfilePermission
from app.models.user import User
from app.services.auth import get_current_user

_CONN = {
    "name": "Test Org",
    "instance_url": "https://myorg.my.salesforce.com",
    "login_url": "https://login.salesforce.com",
    "client_id": "3MVG9test_client_id",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nFAKEKEY\n-----END RSA PRIVATE KEY-----",
    "username": "test@example.com",
    "is_sandbox": False,
}

_IC = {
    "name": "My S3 Bucket",
    "provider": "s3",
    "bucket": "my-bucket",
    "root_prefix": "data/",
    "region": "us-east-1",
    "access_key_id": "AKIAIOSFODNN7EXAMPLE",
    "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
}


def _create(auth_client, payload=None):
    return auth_client.post("/api/input-connections/", json=payload or _IC)


# ── Create ─────────────────────────────────────────────────────────────────────


def test_create_input_connection_returns_201(auth_client):
    resp = _create(auth_client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == _IC["name"]
    assert body["bucket"] == _IC["bucket"]
    assert "id" in body
    assert "created_at" in body


def test_create_input_connection_redacts_secrets(auth_client):
    body = _create(auth_client).json()
    assert "access_key_id" not in body
    assert "secret_access_key" not in body
    assert "session_token" not in body


def test_create_input_connection_missing_required_field_returns_422(auth_client):
    bad = {k: v for k, v in _IC.items() if k != "bucket"}
    resp = auth_client.post("/api/input-connections/", json=bad)
    assert resp.status_code == 422


# ── List ───────────────────────────────────────────────────────────────────────


def test_list_input_connections_empty(auth_client):
    resp = auth_client.get("/api/input-connections/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_input_connections_returns_all(auth_client):
    _create(auth_client)
    _create(auth_client, {**_IC, "name": "Second Bucket"})
    resp = auth_client.get("/api/input-connections/")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ── Get ────────────────────────────────────────────────────────────────────────


def test_get_input_connection_returns_record(auth_client):
    ic_id = _create(auth_client).json()["id"]
    resp = auth_client.get(f"/api/input-connections/{ic_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == ic_id


def test_get_input_connection_not_found_returns_404(auth_client):
    resp = auth_client.get("/api/input-connections/nonexistent-id")
    assert resp.status_code == 404


def test_get_input_connection_omits_secrets(auth_client):
    ic_id = _create(auth_client).json()["id"]
    body = auth_client.get(f"/api/input-connections/{ic_id}").json()
    assert "access_key_id" not in body
    assert "secret_access_key" not in body
    assert "session_token" not in body


# ── Update ─────────────────────────────────────────────────────────────────────


def test_update_input_connection_name(auth_client):
    ic_id = _create(auth_client).json()["id"]
    resp = auth_client.put(f"/api/input-connections/{ic_id}", json={"name": "Updated Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"


def test_update_input_connection_not_found_returns_404(auth_client):
    resp = auth_client.put("/api/input-connections/bad-id", json={"name": "X"})
    assert resp.status_code == 404


def test_update_input_connection_re_encrypts_secrets(auth_client):
    """PUT with new credentials should succeed without exposing them."""
    ic_id = _create(auth_client).json()["id"]
    resp = auth_client.put(
        f"/api/input-connections/{ic_id}",
        json={"access_key_id": "NEWAKIAKEY", "secret_access_key": "NEWsecretkey"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_key_id" not in body
    assert "secret_access_key" not in body


# ── Delete ─────────────────────────────────────────────────────────────────────


def test_delete_input_connection_returns_204(auth_client):
    ic_id = _create(auth_client).json()["id"]
    resp = auth_client.delete(f"/api/input-connections/{ic_id}")
    assert resp.status_code == 204


def test_delete_input_connection_not_found_returns_404(auth_client):
    resp = auth_client.delete("/api/input-connections/nonexistent")
    assert resp.status_code == 404


def test_delete_input_connection_removes_record(auth_client):
    ic_id = _create(auth_client).json()["id"]
    auth_client.delete(f"/api/input-connections/{ic_id}")
    assert auth_client.get(f"/api/input-connections/{ic_id}").status_code == 404


def test_delete_input_connection_returns_409_when_referenced(auth_client):
    """Deleting an InputConnection used by a LoadStep should return 409."""
    ic_id = _create(auth_client).json()["id"]

    # Need a Salesforce connection to create a plan
    conn_id = auth_client.post("/api/connections/", json=_CONN).json()["id"]
    plan_id = auth_client.post(
        "/api/load-plans/",
        json={
            "name": "Test Plan",
            "connection_id": conn_id,
            "max_parallel_jobs": 1,
            "error_threshold_pct": 5.0,
            "abort_on_step_failure": False,
        },
    ).json()["id"]
    auth_client.post(
        f"/api/load-plans/{plan_id}/steps/",
        json={
            "object_name": "Account",
            "operation": "insert",
            "csv_file_pattern": "/data/*.csv",
            "input_connection_id": ic_id,
        },
    )

    resp = auth_client.delete(f"/api/input-connections/{ic_id}")
    assert resp.status_code == 409


# ── Test endpoint ──────────────────────────────────────────────────────────────


def test_test_input_connection_not_found_returns_404(auth_client):
    resp = auth_client.post("/api/input-connections/nonexistent/test")
    assert resp.status_code == 404


def test_test_input_connection_read_only_success(auth_client):
    ic_id = _create(auth_client, {**_IC, "direction": "in"}).json()["id"]

    with patch(
        "app.api.input_connections.asyncio.to_thread",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = auth_client.post(f"/api/input-connections/{ic_id}/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "read access verified" in body["message"]
    assert "write" not in body["message"]


def test_test_input_connection_output_write_success(auth_client):
    ic_id = _create(auth_client, {**_IC, "direction": "out"}).json()["id"]

    with patch(
        "app.api.input_connections.asyncio.to_thread",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = auth_client.post(f"/api/input-connections/{ic_id}/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "read and write access verified" in body["message"]


def test_test_input_connection_both_direction_write_success(auth_client):
    ic_id = _create(auth_client, {**_IC, "direction": "both"}).json()["id"]

    with patch(
        "app.api.input_connections.asyncio.to_thread",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = auth_client.post(f"/api/input-connections/{ic_id}/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "read and write access verified" in body["message"]


def test_test_input_connection_write_access_denied(auth_client):
    import botocore.exceptions

    ic_id = _create(auth_client, {**_IC, "direction": "out"}).json()["id"]

    error_response = {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}
    client_error = botocore.exceptions.ClientError(error_response, "PutObject")

    with patch(
        "app.api.input_connections.asyncio.to_thread",
        new_callable=AsyncMock,
        side_effect=client_error,
    ):
        resp = auth_client.post(f"/api/input-connections/{ic_id}/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "AccessDenied" in body["message"]


def test_test_input_connection_auth_failure(auth_client):
    import botocore.exceptions

    ic_id = _create(auth_client).json()["id"]

    error_response = {"Error": {"Code": "InvalidClientTokenId", "Message": "Bad token"}}
    client_error = botocore.exceptions.ClientError(error_response, "ListObjectsV2")

    with patch(
        "app.api.input_connections.asyncio.to_thread",
        new_callable=AsyncMock,
        side_effect=client_error,
    ):
        resp = auth_client.post(f"/api/input-connections/{ic_id}/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "InvalidClientTokenId" in body["message"]


# ── LoadStep carries input_connection_id ───────────────────────────────────────


def test_load_step_carries_input_connection_id(auth_client):
    """A step created with input_connection_id should return it in GET."""
    ic_id = _create(auth_client).json()["id"]

    conn_id = auth_client.post("/api/connections/", json=_CONN).json()["id"]
    plan_id = auth_client.post(
        "/api/load-plans/",
        json={
            "name": "IC Step Plan",
            "connection_id": conn_id,
            "max_parallel_jobs": 1,
            "error_threshold_pct": 5.0,
            "abort_on_step_failure": False,
        },
    ).json()["id"]

    step_resp = auth_client.post(
        f"/api/load-plans/{plan_id}/steps/",
        json={
            "object_name": "Contact",
            "operation": "insert",
            "csv_file_pattern": "/data/*.csv",
            "input_connection_id": ic_id,
        },
    )
    assert step_resp.status_code == 201
    step = step_resp.json()
    assert step["input_connection_id"] == ic_id

    # Verify via plan detail (which includes steps)
    plan_detail = auth_client.get(f"/api/load-plans/{plan_id}").json()
    load_steps = plan_detail["load_steps"]
    assert len(load_steps) == 1
    assert load_steps[0]["input_connection_id"] == ic_id


# ── RBAC permission tests (SFBL-374) ──────────────────────────────────────────
#
# Mirrors the pattern in test_permission_matrix.py.
# Three helpers build synthetic profile-bearing users and make requests
# with auth_mode patched to "jwt" so require_permission evaluates real keys.


def _make_profile_with_keys(name: str, *keys: str) -> Profile:
    profile = Profile(id=str(uuid.uuid4()), name=name)
    profile.permissions = [ProfilePermission(permission_key=k) for k in keys]
    return profile


def _user_with_keys(*keys: str) -> User:
    profile = _make_profile_with_keys("test-profile", *keys)
    user = User(
        id=str(uuid.uuid4()),
        email="rbac-test@example.com",
        hashed_password="x",
        is_admin=False,
        status="active",
    )
    user.profile = profile
    return user


def _call_as_user(auth_client, user: User, method: str, path: str, body=None):
    """Make a request injecting *user* and patching auth_mode='jwt'."""

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    try:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            fn = getattr(auth_client, method.lower())
            return fn(path, json=body) if body is not None else fn(path)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Hosted-mode: user WITHOUT connections.view gets 403 on read routes ─────────


def test_input_connection_list_requires_view_permission(auth_client):
    """GET / — user without connections.view gets 403."""
    user = _user_with_keys()  # no keys
    resp = _call_as_user(auth_client, user, "GET", "/api/input-connections/")
    assert resp.status_code == 403
    assert resp.json()["detail"]["required_permission"] == "connections.view"


def test_input_connection_get_requires_view_permission(auth_client):
    """GET /{id} — user without connections.view gets 403."""
    # Seed a record as an admin (auth_client already has all permissions)
    ic_id = _create(auth_client).json()["id"]
    user = _user_with_keys()  # no keys
    resp = _call_as_user(auth_client, user, "GET", f"/api/input-connections/{ic_id}")
    assert resp.status_code == 403
    assert resp.json()["detail"]["required_permission"] == "connections.view"


# ── Hosted-mode: user WITH connections.view but NOT connections.manage ─────────


def test_input_connection_create_requires_manage_permission(auth_client):
    """POST / — connections.view is not enough; connections.manage is required."""
    user = _user_with_keys("connections.view")
    resp = _call_as_user(auth_client, user, "POST", "/api/input-connections/", _IC)
    assert resp.status_code == 403
    assert resp.json()["detail"]["required_permission"] == "connections.manage"


def test_input_connection_update_requires_manage_permission(auth_client):
    """PUT /{id} — connections.view is not enough; connections.manage is required."""
    ic_id = _create(auth_client).json()["id"]
    user = _user_with_keys("connections.view")
    resp = _call_as_user(auth_client, user, "PUT", f"/api/input-connections/{ic_id}", {"name": "X"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["required_permission"] == "connections.manage"


def test_input_connection_delete_requires_manage_permission(auth_client):
    """DELETE /{id} — connections.view is not enough; connections.manage is required."""
    ic_id = _create(auth_client).json()["id"]
    user = _user_with_keys("connections.view")
    resp = _call_as_user(auth_client, user, "DELETE", f"/api/input-connections/{ic_id}")
    assert resp.status_code == 403
    assert resp.json()["detail"]["required_permission"] == "connections.manage"


def test_input_connection_test_requires_manage_permission(auth_client):
    """POST /{id}/test — connections.view is not enough; connections.manage is required."""
    ic_id = _create(auth_client).json()["id"]
    user = _user_with_keys("connections.view")
    resp = _call_as_user(auth_client, user, "POST", f"/api/input-connections/{ic_id}/test")
    assert resp.status_code == 403
    assert resp.json()["detail"]["required_permission"] == "connections.manage"


# ── Desktop mode: auth_mode=none bypasses all permission checks → 200 ─────────


def _call_desktop(auth_client, method: str, path: str, body=None):
    """Make a request with auth_mode='none' (desktop profile — no permission check)."""
    user = User(
        id=str(uuid.uuid4()),
        email="desktop@localhost",
        hashed_password="x",
        is_admin=True,
        status="active",
    )
    user.profile = None  # Desktop virtual user has no profile

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    try:
        with patch("app.config.settings") as mock_settings:
            mock_settings.auth_mode = "none"
            mock_settings.input_storage_mode = "local"
            fn = getattr(auth_client, method.lower())
            return fn(path, json=body) if body is not None else fn(path)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_input_connection_list_desktop_mode_returns_200(auth_client):
    """GET / — desktop mode (auth_mode=none) returns 200 for all routes."""
    resp = _call_desktop(auth_client, "GET", "/api/input-connections/")
    assert resp.status_code == 200


def test_input_connection_create_desktop_mode_returns_201(auth_client):
    """POST / — desktop mode returns 201."""
    resp = _call_desktop(auth_client, "POST", "/api/input-connections/", _IC)
    assert resp.status_code == 201


def test_input_connection_get_desktop_mode_returns_200(auth_client):
    """GET /{id} — desktop mode returns 200."""
    ic_id = _create(auth_client).json()["id"]
    resp = _call_desktop(auth_client, "GET", f"/api/input-connections/{ic_id}")
    assert resp.status_code == 200


def test_input_connection_update_desktop_mode_returns_200(auth_client):
    """PUT /{id} — desktop mode returns 200."""
    ic_id = _create(auth_client).json()["id"]
    resp = _call_desktop(auth_client, "PUT", f"/api/input-connections/{ic_id}", {"name": "Desktop Updated"})
    assert resp.status_code == 200


def test_input_connection_delete_desktop_mode_returns_204(auth_client):
    """DELETE /{id} — desktop mode returns 204."""
    ic_id = _create(auth_client).json()["id"]
    resp = _call_desktop(auth_client, "DELETE", f"/api/input-connections/{ic_id}")
    assert resp.status_code == 204


def test_input_connection_test_desktop_mode_returns_200(auth_client):
    """POST /{id}/test — desktop mode returns 200 (mocked S3 call)."""
    ic_id = _create(auth_client).json()["id"]
    with patch(
        "app.api.input_connections.asyncio.to_thread",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = _call_desktop(auth_client, "POST", f"/api/input-connections/{ic_id}/test")
    assert resp.status_code == 200
