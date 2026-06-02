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
                json={"status": "ok"},
                headers={"content-type": "application/json"},
            )
        )
        async with BulkLoaderClient(settings_with_url) as client:
            client._http = httpx.AsyncClient(transport=transport)
            resp = await client.get("/api/health/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

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

    def test_pat_mode_requires_token_at_startup(self) -> None:
        """PAT mode without a token now fails config validation (SFBL-371)."""
        from pydantic import ValidationError
        with pytest.raises((ValueError, ValidationError)):
            McpSettings(
                auth_mode="pat",
                bulkloader_base_url="http://hosted",
                bulkloader_pat=None,
            )


# ── Health tool tests (mocked client) ────────────────────────────────────────

class TestHealthTool:
    """health tool returns readable text; errors are safe (no stack traces)."""

    @pytest.mark.asyncio
    async def test_health_success(self) -> None:
        from sf_bulk_loader_mcp.tools.health import check_health, format_health_result

        mock_client = MagicMock()
        mock_response = MagicMock()
        # Real /api/health/ready shape: a `status` field ("ok" => ready),
        # NOT a `ready` boolean (the original mock used the wrong shape, which
        # is why this test passed while the live tool reported "NOT ready").
        mock_response.json.return_value = {"status": "ok", "db": "ok"}
        mock_client.get = AsyncMock(return_value=mock_response)

        payload = await check_health(mock_client)
        text = format_health_result(payload)

        assert payload["status"] == "ok"
        assert "Backend is ready." in text
        assert "NOT ready" not in text
        assert "db" in text

    @pytest.mark.asyncio
    async def test_health_not_ready(self) -> None:
        from sf_bulk_loader_mcp.tools.health import format_health_result

        payload = {"status": "error", "db": "unavailable"}
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

        text = format_health_result({"status": "ok"})
        assert text == "Backend is ready. (status='ok')"


# ── Transport-error mapping tests (SFBL-371 hardening) ────────────────────────

class TestTransportErrorMapping:
    """Connection/timeout failures map to a clean McpHttpError, never a raw httpx
    traceback — upholding the module's 'no raw stack traces' guarantee."""

    @pytest.fixture
    def settings_with_url(self) -> McpSettings:
        return McpSettings(bulkloader_base_url="http://unreachable")

    @pytest.mark.asyncio
    async def test_connect_error_maps_to_mcp_http_error(
        self, settings_with_url: McpSettings
    ) -> None:
        def _raise(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        transport = httpx.MockTransport(_raise)
        async with BulkLoaderClient(settings_with_url) as client:
            client._http = httpx.AsyncClient(transport=transport)
            with pytest.raises(McpHttpError) as exc_info:
                await client.get("/api/connections")
        err = exc_info.value
        # Sentinel status 0 = transport failure (no HTTP response received).
        assert err.status_code == 0
        text = err.to_tool_error_text()
        assert "Connection error" in text
        assert "BULKLOADER_BASE_URL" in text
        assert "Traceback" not in text

    @pytest.mark.asyncio
    async def test_timeout_maps_to_mcp_http_error(
        self, settings_with_url: McpSettings
    ) -> None:
        def _raise(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out")

        transport = httpx.MockTransport(_raise)
        async with BulkLoaderClient(settings_with_url) as client:
            client._http = httpx.AsyncClient(transport=transport)
            with pytest.raises(McpHttpError) as exc_info:
                await client.post("/api/load-plans/", json={})
        assert exc_info.value.status_code == 0


# ── Auth-failure observability tests (SFBL-371) ───────────────────────────────

class TestPatAuthFailureLogging:
    """401 in PAT mode emits a structured WARNING; the PAT value is never logged."""

    def _pat_settings(self) -> McpSettings:
        return McpSettings(
            auth_mode="pat",
            bulkloader_base_url="http://hosted",
            bulkloader_pat="sfbl_pat_secret_token",
        )

    @pytest.mark.asyncio
    async def test_401_in_pat_mode_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(401, content=b"Unauthorized")
        )
        import logging
        with caplog.at_level(logging.WARNING, logger="sf_bulk_loader_mcp.client"):
            async with BulkLoaderClient(self._pat_settings()) as client:
                client._http = httpx.AsyncClient(transport=transport)
                with pytest.raises(McpHttpError) as exc_info:
                    await client.get("/api/connections")
            assert exc_info.value.status_code == 401

        assert any(
            "MCP_PAT_AUTH_FAILURE" in r.getMessage() or
            getattr(r, "event_name", None) == "MCP_PAT_AUTH_FAILURE"
            for r in caplog.records
        ), "Expected MCP_PAT_AUTH_FAILURE event in log records"

    @pytest.mark.asyncio
    async def test_401_log_never_contains_pat_value(self, caplog: pytest.LogCaptureFixture) -> None:
        """The PAT value must never appear in any log record — sanitization rule."""
        transport = httpx.MockTransport(
            lambda request: httpx.Response(401, content=b"Unauthorized")
        )
        import logging
        with caplog.at_level(logging.WARNING, logger="sf_bulk_loader_mcp.client"):
            async with BulkLoaderClient(self._pat_settings()) as client:
                client._http = httpx.AsyncClient(transport=transport)
                with pytest.raises(McpHttpError):
                    await client.post("/api/load-plans/", json={})

        full_log_text = " ".join(
            r.getMessage() + str(r.__dict__)
            for r in caplog.records
        )
        assert "sfbl_pat_secret_token" not in full_log_text, (
            "PAT value must NEVER appear in log output"
        )

    @pytest.mark.asyncio
    async def test_401_in_none_mode_does_not_log_auth_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """401 in desktop/none mode must not log an auth-failure event."""
        settings = McpSettings(bulkloader_base_url="http://desktop")
        transport = httpx.MockTransport(
            lambda request: httpx.Response(401, content=b"Unauthorized")
        )
        import logging
        with caplog.at_level(logging.WARNING, logger="sf_bulk_loader_mcp.client"):
            async with BulkLoaderClient(settings) as client:
                client._http = httpx.AsyncClient(transport=transport)
                with pytest.raises(McpHttpError):
                    await client.get("/api/connections")

        assert not any(
            getattr(r, "event_name", None) == "MCP_PAT_AUTH_FAILURE"
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_non_401_error_in_pat_mode_does_not_log_auth_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """403 / 500 etc. in PAT mode must not emit the auth-failure event."""
        transport = httpx.MockTransport(
            lambda request: httpx.Response(403, content=b"Forbidden")
        )
        import logging
        with caplog.at_level(logging.WARNING, logger="sf_bulk_loader_mcp.client"):
            async with BulkLoaderClient(self._pat_settings()) as client:
                client._http = httpx.AsyncClient(transport=transport)
                with pytest.raises(McpHttpError) as exc_info:
                    await client.get("/api/connections")
            assert exc_info.value.status_code == 403

        assert not any(
            getattr(r, "event_name", None) == "MCP_PAT_AUTH_FAILURE"
            for r in caplog.records
        )
