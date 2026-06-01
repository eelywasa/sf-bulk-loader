"""Tests for client.py — HTTP→MCP error mapping, no stack trace leakage.

Uses httpx mock transport to simulate backend responses.  No live server needed.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sf_bulk_loader_mcp.client import BulkLoaderClient, McpHttpError, _map_http_error
from sf_bulk_loader_mcp.config import McpSettings


# ── Error mapping unit tests (sync, no client needed) ─────────────────────────

class TestMapHttpError:
    """_map_http_error() converts httpx.Response to structured McpHttpError."""

    def _make_response(self, status: int, body: Any = None) -> httpx.Response:
        if body is None:
            content = b""
            headers = {}
        elif isinstance(body, dict):
            content = json.dumps(body).encode()
            headers = {"content-type": "application/json"}
        else:
            content = str(body).encode()
            headers = {}
        return httpx.Response(status_code=status, content=content, headers=headers)

    def test_404_no_detail(self) -> None:
        response = self._make_response(404)
        err = _map_http_error(response)
        assert err.status_code == 404
        assert "not found" in err.message.lower()
        assert err.detail is None

    def test_422_with_json_detail(self) -> None:
        response = self._make_response(422, {"detail": "field x is required"})
        err = _map_http_error(response)
        assert err.status_code == 422
        assert err.detail == "field x is required"
        assert "validation" in err.message.lower()

    def test_500_server_error(self) -> None:
        response = self._make_response(500, {"detail": "internal server error"})
        err = _map_http_error(response)
        assert err.status_code == 500
        assert "server error" in err.message.lower()

    def test_non_json_body_does_not_raise(self) -> None:
        response = self._make_response(500, "plain text error")
        err = _map_http_error(response)
        assert err.status_code == 500
        assert err.detail is None

    def test_401_unauthorized(self) -> None:
        response = self._make_response(401)
        err = _map_http_error(response)
        assert "unauthorized" in err.message.lower()

    def test_403_forbidden(self) -> None:
        response = self._make_response(403)
        err = _map_http_error(response)
        assert "forbidden" in err.message.lower()

    def test_429_rate_limited(self) -> None:
        response = self._make_response(429)
        err = _map_http_error(response)
        assert "rate" in err.message.lower()

    def test_to_tool_error_text_includes_status(self) -> None:
        response = self._make_response(404, {"detail": "resource gone"})
        err = _map_http_error(response)
        text = err.to_tool_error_text()
        assert "404" in text
        assert "resource gone" in text

    def test_no_stack_trace_in_error_text(self) -> None:
        response = self._make_response(500)
        err = _map_http_error(response)
        text = err.to_tool_error_text()
        # Stack traces contain "Traceback" or "File "
        assert "Traceback" not in text
        assert 'File "' not in text


# ── Client integration tests (mocked httpx) ───────────────────────────────────

class TestBulkLoaderClientErrorHandling:
    """BulkLoaderClient raises McpHttpError on non-2xx; no leakage."""

    @pytest.fixture
    def settings_with_url(self) -> McpSettings:
        return McpSettings(bulkloader_base_url="http://testserver")

    @pytest.mark.asyncio
    async def test_get_raises_mcp_http_error_on_404(
        self, settings_with_url: McpSettings
    ) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                404,
                json={"detail": "not found"},
                headers={"content-type": "application/json"},
            )
        )
        async with BulkLoaderClient(settings_with_url) as client:
            client._http = httpx.AsyncClient(transport=transport)
            with pytest.raises(McpHttpError) as exc_info:
                await client.get("/api/plans/missing")
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_raises_mcp_http_error_on_500(
        self, settings_with_url: McpSettings
    ) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(500, content=b"Internal Server Error")
        )
        async with BulkLoaderClient(settings_with_url) as client:
            client._http = httpx.AsyncClient(transport=transport)
            with pytest.raises(McpHttpError) as exc_info:
                await client.get("/api/runs")
            assert exc_info.value.status_code == 500
            # Confirm no stack trace in the error
            assert "Traceback" not in exc_info.value.to_tool_error_text()

    @pytest.mark.asyncio
    async def test_get_success_returns_response(
        self, settings_with_url: McpSettings
    ) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"ready": True},
                headers={"content-type": "application/json"},
            )
        )
        async with BulkLoaderClient(settings_with_url) as client:
            client._http = httpx.AsyncClient(transport=transport)
            resp = await client.get("/api/health/ready")
        assert resp.status_code == 200
        assert resp.json()["ready"] is True

    @pytest.mark.asyncio
    async def test_422_raises_with_detail(
        self, settings_with_url: McpSettings
    ) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                422,
                json={"detail": [{"loc": ["body", "name"], "msg": "field required"}]},
                headers={"content-type": "application/json"},
            )
        )
        async with BulkLoaderClient(settings_with_url) as client:
            client._http = httpx.AsyncClient(transport=transport)
            with pytest.raises(McpHttpError) as exc_info:
                await client.post("/api/plans", json={"bad": "data"})
            assert exc_info.value.status_code == 422
            assert exc_info.value.detail is not None

    def test_none_mode_does_not_inject_auth_header(
        self, settings_with_url: McpSettings
    ) -> None:
        client = BulkLoaderClient(settings_with_url)
        headers = client._build_headers()
        assert "Authorization" not in headers

    def test_pat_mode_injects_bearer_header(self) -> None:
        settings = McpSettings(
            auth_mode="pat",
            bulkloader_base_url="http://hosted",
            bulkloader_pat="mytoken",
        )
        client = BulkLoaderClient(settings)
        headers = client._build_headers()
        assert headers.get("Authorization") == "Bearer mytoken"

    def test_pat_mode_without_token_does_not_inject_header(self) -> None:
        settings = McpSettings(
            auth_mode="pat",
            bulkloader_base_url="http://hosted",
            bulkloader_pat=None,
        )
        client = BulkLoaderClient(settings)
        headers = client._build_headers()
        assert "Authorization" not in headers


# ── Health tool tests (mocked client) ────────────────────────────────────────

class TestHealthTool:
    """health tool returns readable text; errors are safe (no stack traces)."""

    @pytest.mark.asyncio
    async def test_health_success(self) -> None:
        from sf_bulk_loader_mcp.tools.health import check_health, format_health_result

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"ready": True, "db": "ok"}
        mock_client.get = AsyncMock(return_value=mock_response)

        payload = await check_health(mock_client)
        text = format_health_result(payload)

        assert payload["ready"] is True
        assert "ready" in text.lower()
        assert "db" in text

    @pytest.mark.asyncio
    async def test_health_not_ready(self) -> None:
        from sf_bulk_loader_mcp.tools.health import format_health_result

        payload = {"ready": False, "db": "unavailable"}
        text = format_health_result(payload)
        assert "NOT ready" in text

    @pytest.mark.asyncio
    async def test_health_raises_mcp_http_error_on_backend_error(self) -> None:
        from sf_bulk_loader_mcp.tools.health import check_health

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=McpHttpError(503, "Service Unavailable"))

        with pytest.raises(McpHttpError):
            await check_health(mock_client)

    def test_format_health_no_extra_fields(self) -> None:
        from sf_bulk_loader_mcp.tools.health import format_health_result

        text = format_health_result({"ready": True})
        assert text == "Backend is ready."
