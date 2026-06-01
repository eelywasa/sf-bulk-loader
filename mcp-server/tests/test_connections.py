"""Tests for tools/connections.py — Salesforce + storage connection tools.

Uses mocked BulkLoaderClient (MagicMock + AsyncMock) so no live server is
needed.  Covers:
  - Happy path: correct endpoint + method called, formatted output
  - Error mapping: 404/422/500 → structured text, no traceback
  - Secrets redaction: connection responses with redacted fields stay redacted;
    create payloads' secrets are NOT echoed in tool output
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from sf_bulk_loader_mcp.client import McpHttpError
from sf_bulk_loader_mcp.tools.connections import (
    # Salesforce
    list_connections,
    format_list_connections,
    get_connection,
    format_connection,
    create_connection,
    format_create_connection,
    update_connection,
    format_update_connection,
    check_connection,
    format_test_connection,
    list_sobjects,
    format_list_sobjects,
    # Storage
    list_input_connections,
    format_list_input_connections,
    get_input_connection,
    format_input_connection,
    create_input_connection,
    format_create_input_connection,
    update_input_connection,
    format_update_input_connection,
    check_input_connection,
    format_test_input_connection,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_client(return_value: object) -> MagicMock:
    """Return a mock BulkLoaderClient whose HTTP methods return *return_value*."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = return_value
    for method in ("get", "post", "put", "delete"):
        setattr(mock_client, method, AsyncMock(return_value=mock_response))
    return mock_client


def _make_error_client(status: int, message: str, detail: object = None) -> MagicMock:
    """Return a mock client that raises McpHttpError on every HTTP method."""
    mock_client = MagicMock()
    exc = McpHttpError(status, message, detail)
    for method in ("get", "post", "put", "delete"):
        setattr(mock_client, method, AsyncMock(side_effect=exc))
    return mock_client


# ── Sample fixtures ────────────────────────────────────────────────────────────


SF_CONN = {
    "id": "abc-123",
    "name": "Prod Org",
    "instance_url": "https://myorg.my.salesforce.com",
    "login_url": "https://login.salesforce.com",
    "username": "user@example.com",
    "is_sandbox": False,
    "client_id": "3MVG...",
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-02T00:00:00",
}

INPUT_CONN = {
    "id": "ic-456",
    "name": "My S3 Bucket",
    "provider": "s3",
    "bucket": "my-data-bucket",
    "root_prefix": "data/",
    "region": "us-east-1",
    "direction": "in",
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-02T00:00:00",
}


# ── Salesforce connection — happy path ─────────────────────────────────────────


class TestListConnections:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(self) -> None:
        client = _make_client([SF_CONN])
        await list_connections(client)
        client.get.assert_called_once_with("/api/connections/")

    @pytest.mark.asyncio
    async def test_returns_payload(self) -> None:
        client = _make_client([SF_CONN])
        result = await list_connections(client)
        assert result == [SF_CONN]

    def test_format_non_empty(self) -> None:
        text = format_list_connections([SF_CONN])
        assert "Prod Org" in text
        assert "abc-123" in text
        assert "1" in text  # count

    def test_format_empty(self) -> None:
        text = format_list_connections([])
        assert "No Salesforce connections" in text


class TestGetConnection:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(self) -> None:
        client = _make_client(SF_CONN)
        await get_connection(client, "abc-123")
        client.get.assert_called_once_with("/api/connections/abc-123")

    def test_format_includes_key_fields(self) -> None:
        text = format_connection(SF_CONN)
        assert "Prod Org" in text
        assert "abc-123" in text
        assert "user@example.com" in text
        assert "https://myorg.my.salesforce.com" in text

    def test_format_private_key_absent(self) -> None:
        """The API never returns private_key; the formatter must not fabricate it."""
        text = format_connection(SF_CONN)
        assert "private_key" not in text


class TestCreateConnection:
    CREATE_BODY = {
        "name": "New Org",
        "instance_url": "https://new.my.salesforce.com",
        "login_url": "https://login.salesforce.com",
        "client_id": "3MVG...",
        "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----",
        "username": "new@example.com",
        "is_sandbox": False,
    }

    @pytest.mark.asyncio
    async def test_calls_post_endpoint(self) -> None:
        client = _make_client(SF_CONN)
        await create_connection(client, self.CREATE_BODY)
        client.post.assert_called_once()
        call_args = client.post.call_args
        assert call_args[0][0] == "/api/connections/"

    @pytest.mark.asyncio
    async def test_private_key_not_in_output(self) -> None:
        """The private_key sent in the request body must never appear in formatted output."""
        client = _make_client(SF_CONN)
        payload = await create_connection(client, self.CREATE_BODY)
        text = format_create_connection(payload)
        # The API response (SF_CONN) never contains private_key
        assert "BEGIN RSA PRIVATE KEY" not in text
        assert "private_key" not in text

    def test_format_create_includes_created_wording(self) -> None:
        text = format_create_connection(SF_CONN)
        assert "created" in text.lower()
        assert "Prod Org" in text


class TestUpdateConnection:
    @pytest.mark.asyncio
    async def test_calls_put_endpoint(self) -> None:
        client = _make_client(SF_CONN)
        await update_connection(client, "abc-123", {"name": "Renamed"})
        client.put.assert_called_once_with(
            "/api/connections/abc-123", json={"name": "Renamed"}
        )

    def test_format_update_includes_updated_wording(self) -> None:
        text = format_update_connection(SF_CONN)
        assert "updated" in text.lower()


class TestTestConnection:
    @pytest.mark.asyncio
    async def test_calls_post_test_endpoint(self) -> None:
        client = _make_client({"success": True, "message": "Connection successful", "instance_url": "https://myorg.my.salesforce.com"})
        await check_connection(client, "abc-123")
        client.post.assert_called_once_with("/api/connections/abc-123/test")

    def test_format_success(self) -> None:
        payload = {"success": True, "message": "Connection successful", "instance_url": "https://x.sf.com"}
        text = format_test_connection(payload)
        assert "PASSED" in text
        assert "Connection successful" in text

    def test_format_failure(self) -> None:
        payload = {"success": False, "message": "Auth failed"}
        text = format_test_connection(payload)
        assert "FAILED" in text
        assert "Auth failed" in text


class TestListSobjects:
    SOBJECTS = ["Account", "Contact", "Lead", "Opportunity"]

    @pytest.mark.asyncio
    async def test_calls_objects_endpoint(self) -> None:
        client = _make_client(self.SOBJECTS)
        await list_sobjects(client, "abc-123")
        client.get.assert_called_once_with("/api/connections/abc-123/objects")

    def test_format_non_empty(self) -> None:
        text = format_list_sobjects("abc-123", self.SOBJECTS)
        assert "Account" in text
        assert "4 total" in text

    def test_format_truncates_long_lists(self) -> None:
        many = [f"Object{i}" for i in range(20)]
        text = format_list_sobjects("abc-123", many)
        assert "more" in text

    def test_format_empty(self) -> None:
        text = format_list_sobjects("abc-123", [])
        assert "No SObjects" in text


# ── Storage (input) connections — happy path ───────────────────────────────────


class TestListInputConnections:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint_no_filter(self) -> None:
        client = _make_client([INPUT_CONN])
        await list_input_connections(client)
        client.get.assert_called_once_with("/api/input-connections/", params={})

    @pytest.mark.asyncio
    async def test_calls_correct_endpoint_with_direction(self) -> None:
        client = _make_client([INPUT_CONN])
        await list_input_connections(client, direction="in")
        client.get.assert_called_once_with("/api/input-connections/", params={"direction": "in"})

    def test_format_non_empty(self) -> None:
        text = format_list_input_connections([INPUT_CONN])
        assert "My S3 Bucket" in text
        assert "ic-456" in text

    def test_format_empty(self) -> None:
        text = format_list_input_connections([])
        assert "No storage connections" in text


class TestGetInputConnection:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(self) -> None:
        client = _make_client(INPUT_CONN)
        await get_input_connection(client, "ic-456")
        client.get.assert_called_once_with("/api/input-connections/ic-456")

    def test_format_includes_key_fields(self) -> None:
        text = format_input_connection(INPUT_CONN)
        assert "My S3 Bucket" in text
        assert "ic-456" in text
        assert "my-data-bucket" in text
        assert "us-east-1" in text

    def test_format_no_secret_fields(self) -> None:
        """The API never returns access_key_id etc.; the formatter must not fabricate them."""
        text = format_input_connection(INPUT_CONN)
        assert "access_key_id" not in text
        assert "secret_access_key" not in text
        assert "session_token" not in text


class TestCreateInputConnection:
    CREATE_BODY = {
        "name": "New S3",
        "provider": "s3",
        "bucket": "new-bucket",
        "region": "eu-west-1",
        "direction": "in",
        "access_key_id": "AKIAIOSFODNN7EXAMPLE",
        "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    }

    @pytest.mark.asyncio
    async def test_calls_post_endpoint(self) -> None:
        client = _make_client(INPUT_CONN)
        await create_input_connection(client, self.CREATE_BODY)
        client.post.assert_called_once()
        assert client.post.call_args[0][0] == "/api/input-connections/"

    @pytest.mark.asyncio
    async def test_credentials_not_echoed_in_output(self) -> None:
        """Secrets sent in the request body must never appear in formatted output."""
        client = _make_client(INPUT_CONN)
        payload = await create_input_connection(client, self.CREATE_BODY)
        text = format_create_input_connection(payload)
        # The API response (INPUT_CONN) never contains credentials
        assert "AKIAIOSFODNN7EXAMPLE" not in text
        assert "wJalrXUtnFEMI" not in text
        assert "secret_access_key" not in text

    def test_format_create_includes_created_wording(self) -> None:
        text = format_create_input_connection(INPUT_CONN)
        assert "created" in text.lower()


class TestUpdateInputConnection:
    @pytest.mark.asyncio
    async def test_calls_put_endpoint(self) -> None:
        client = _make_client(INPUT_CONN)
        await update_input_connection(client, "ic-456", {"bucket": "new-bucket"})
        client.put.assert_called_once_with(
            "/api/input-connections/ic-456", json={"bucket": "new-bucket"}
        )

    def test_format_update_includes_updated_wording(self) -> None:
        text = format_update_input_connection(INPUT_CONN)
        assert "updated" in text.lower()


class TestTestInputConnection:
    @pytest.mark.asyncio
    async def test_calls_post_test_endpoint(self) -> None:
        client = _make_client({"success": True, "message": "S3 connection successful"})
        await check_input_connection(client, "ic-456")
        client.post.assert_called_once_with("/api/input-connections/ic-456/test")

    def test_format_success(self) -> None:
        payload = {"success": True, "message": "S3 connection successful"}
        text = format_test_input_connection(payload)
        assert "PASSED" in text
        assert "S3 connection successful" in text

    def test_format_failure(self) -> None:
        payload = {"success": False, "message": "Access Denied"}
        text = format_test_input_connection(payload)
        assert "FAILED" in text
        assert "Access Denied" in text


# ── Error mapping (McpHttpError → structured text, no traceback) ───────────────


class TestSalesforceConnectionErrors:
    @pytest.mark.asyncio
    async def test_list_connections_404(self) -> None:
        client = _make_error_client(404, "Resource not found")
        with pytest.raises(McpHttpError) as exc_info:
            await list_connections(client)
        text = exc_info.value.to_tool_error_text()
        assert "404" in text
        assert "Traceback" not in text
        assert 'File "' not in text

    @pytest.mark.asyncio
    async def test_get_connection_404(self) -> None:
        client = _make_error_client(404, "Resource not found", "Connection not found")
        with pytest.raises(McpHttpError) as exc_info:
            await get_connection(client, "missing-id")
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Connection not found"

    @pytest.mark.asyncio
    async def test_create_connection_422(self) -> None:
        client = _make_error_client(422, "Unprocessable request — validation error", [{"msg": "field required"}])
        with pytest.raises(McpHttpError) as exc_info:
            await create_connection(client, {})
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_test_connection_500(self) -> None:
        client = _make_error_client(500, "Backend server error (HTTP 500)")
        with pytest.raises(McpHttpError) as exc_info:
            await check_connection(client, "abc-123")
        text = exc_info.value.to_tool_error_text()
        assert "500" in text
        assert "Traceback" not in text


class TestInputConnectionErrors:
    @pytest.mark.asyncio
    async def test_get_input_connection_404(self) -> None:
        client = _make_error_client(404, "Resource not found", "Input connection not found")
        with pytest.raises(McpHttpError) as exc_info:
            await get_input_connection(client, "missing")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_create_input_connection_422(self) -> None:
        client = _make_error_client(422, "Unprocessable request — validation error")
        with pytest.raises(McpHttpError) as exc_info:
            await create_input_connection(client, {})
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_test_input_connection_500(self) -> None:
        client = _make_error_client(500, "Backend server error (HTTP 500)")
        with pytest.raises(McpHttpError) as exc_info:
            await check_input_connection(client, "ic-456")
        text = exc_info.value.to_tool_error_text()
        assert "500" in text
        assert "Traceback" not in text


# ── Secrets redaction assertions ───────────────────────────────────────────────


class TestSecretsRedaction:
    """Verify credential fields are never surfaced in formatted output."""

    def test_sf_connection_response_omits_private_key(self) -> None:
        """The API response never has private_key; the formatter must not add it."""
        # Simulate the WORST-CASE scenario: a response that accidentally includes
        # a private_key field.  The formatter should still not highlight it.
        conn_with_hypothetical_leak = {**SF_CONN, "private_key": "SHOULD_NOT_APPEAR"}
        text = format_connection(conn_with_hypothetical_leak)
        # format_connection only outputs known display fields, not private_key
        assert "SHOULD_NOT_APPEAR" not in text

    def test_input_connection_response_omits_credentials(self) -> None:
        """Formatter must not surface credential fields even if response contains them."""
        ic_with_hypothetical_leak = {
            **INPUT_CONN,
            "access_key_id": "AKIALEAK",
            "secret_access_key": "SECRET_LEAK",
            "session_token": "TOKEN_LEAK",
        }
        text = format_input_connection(ic_with_hypothetical_leak)
        assert "AKIALEAK" not in text
        assert "SECRET_LEAK" not in text
        assert "TOKEN_LEAK" not in text

    def test_create_sf_connection_body_secret_not_in_output(self) -> None:
        """private_key in the create request body is not echoed in the formatted response."""
        # The formatted response is built from the API payload (SF_CONN),
        # which never includes private_key.
        text = format_create_connection(SF_CONN)
        assert "private_key" not in text
        assert "BEGIN" not in text  # PEM header not echoed

    def test_create_input_connection_body_secrets_not_in_output(self) -> None:
        """AWS credentials in create request are not echoed in the formatted response."""
        text = format_create_input_connection(INPUT_CONN)
        assert "access_key_id" not in text
        assert "secret_access_key" not in text
        assert "session_token" not in text
