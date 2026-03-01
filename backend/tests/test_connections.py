"""Tests for the /api/connections endpoints."""

from unittest.mock import AsyncMock, patch

import pytest

# Payload helpers
_CONN = {
    "name": "Test Org",
    "instance_url": "https://myorg.my.salesforce.com",
    "login_url": "https://login.salesforce.com",
    "client_id": "3MVG9test_client_id",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nFAKEKEY\n-----END RSA PRIVATE KEY-----",
    "username": "test@example.com",
    "is_sandbox": False,
}


def _create(client, payload=None):
    return client.post("/api/connections/", json=payload or _CONN)


# ── Create ─────────────────────────────────────────────────────────────────────


def test_create_connection_returns_201(client):
    resp = _create(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == _CONN["name"]
    assert body["username"] == _CONN["username"]
    assert "id" in body
    assert "created_at" in body


def test_create_connection_redacts_secrets(client):
    resp = _create(client)
    body = resp.json()
    assert "private_key" not in body
    assert "access_token" not in body


def test_create_connection_missing_required_field_returns_422(client):
    bad = {k: v for k, v in _CONN.items() if k != "username"}
    resp = client.post("/api/connections/", json=bad)
    assert resp.status_code == 422


# ── List ───────────────────────────────────────────────────────────────────────


def test_list_connections_empty(client):
    resp = client.get("/api/connections/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_connections_returns_all(client):
    _create(client)
    _create(client, {**_CONN, "name": "Second Org"})
    resp = client.get("/api/connections/")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ── Get ────────────────────────────────────────────────────────────────────────


def test_get_connection_returns_record(client):
    conn_id = _create(client).json()["id"]
    resp = client.get(f"/api/connections/{conn_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == conn_id


def test_get_connection_not_found_returns_404(client):
    resp = client.get("/api/connections/nonexistent-id")
    assert resp.status_code == 404


def test_get_connection_omits_secrets(client):
    conn_id = _create(client).json()["id"]
    body = client.get(f"/api/connections/{conn_id}").json()
    assert "private_key" not in body
    assert "access_token" not in body


# ── Update ─────────────────────────────────────────────────────────────────────


def test_update_connection_name(client):
    conn_id = _create(client).json()["id"]
    resp = client.put(f"/api/connections/{conn_id}", json={"name": "Updated Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"


def test_update_connection_not_found_returns_404(client):
    resp = client.put("/api/connections/bad-id", json={"name": "X"})
    assert resp.status_code == 404


def test_update_connection_private_key_is_re_encrypted(client):
    """PUT with a new private_key should succeed without exposing the key."""
    conn_id = _create(client).json()["id"]
    new_key = "-----BEGIN RSA PRIVATE KEY-----\nNEWKEY\n-----END RSA PRIVATE KEY-----"
    resp = client.put(f"/api/connections/{conn_id}", json={"private_key": new_key})
    assert resp.status_code == 200
    assert "private_key" not in resp.json()


# ── Delete ─────────────────────────────────────────────────────────────────────


def test_delete_connection_returns_204(client):
    conn_id = _create(client).json()["id"]
    resp = client.delete(f"/api/connections/{conn_id}")
    assert resp.status_code == 204


def test_delete_connection_not_found_returns_404(client):
    resp = client.delete("/api/connections/nonexistent")
    assert resp.status_code == 404


def test_delete_connection_removes_record(client):
    conn_id = _create(client).json()["id"]
    client.delete(f"/api/connections/{conn_id}")
    assert client.get(f"/api/connections/{conn_id}").status_code == 404


# ── Test connectivity ──────────────────────────────────────────────────────────


def test_test_connection_not_found_returns_404(client):
    resp = client.post("/api/connections/nonexistent/test")
    assert resp.status_code == 404


def test_test_connection_auth_failure_returns_success_false(client):
    conn_id = _create(client).json()["id"]

    from app.services.salesforce_auth import AuthError

    with patch(
        "app.api.connections.get_access_token",
        new_callable=AsyncMock,
        side_effect=AuthError("invalid_grant"),
    ):
        resp = client.post(f"/api/connections/{conn_id}/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "invalid_grant" in body["message"]


def test_test_connection_sf_api_success(client):
    conn_id = _create(client).json()["id"]

    mock_response = AsyncMock()
    mock_response.status_code = 200

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    mock_http.get = AsyncMock(return_value=mock_response)

    with (
        patch("app.api.connections.get_access_token", new_callable=AsyncMock, return_value="tok"),
        patch("app.api.connections.httpx.AsyncClient", return_value=mock_http),
    ):
        resp = client.post(f"/api/connections/{conn_id}/test")

    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_test_connection_sf_api_error_returns_success_false(client):
    conn_id = _create(client).json()["id"]

    mock_response = AsyncMock()
    mock_response.status_code = 401

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    mock_http.get = AsyncMock(return_value=mock_response)

    with (
        patch("app.api.connections.get_access_token", new_callable=AsyncMock, return_value="tok"),
        patch("app.api.connections.httpx.AsyncClient", return_value=mock_http),
    ):
        resp = client.post(f"/api/connections/{conn_id}/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "401" in body["message"]
