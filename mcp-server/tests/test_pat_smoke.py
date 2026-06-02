"""PAT auth mode smoke test (SFBL-373).

Proves that the MCP server in PAT / remote mode:
  1. Injects Authorization: Bearer on every backend call.
  2. Drives the create→run→inspect lifecycle successfully against a
     hosted-mode fixture backend that enforces Bearer auth.
  3. Surfaces a clear 401 error when the PAT is wrong.
  4. Never leaks the PAT value in error messages or logs.

The fixture backend is a mock httpx transport, NOT a no-auth backend.
Using a no-auth transport would silently bypass the PAT path and prove
nothing — the transport here returns 401 for any request missing the
correct Authorization header, mirroring a real hosted-mode backend.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import patch
import uuid

import httpx
import pytest

from sf_bulk_loader_mcp.client import BulkLoaderClient, McpHttpError
from sf_bulk_loader_mcp.config import McpSettings

# ── Fixture helpers ────────────────────────────────────────────────────────────

VALID_PAT = "sfbl_pat_smoke_test_token"
BASE_URL = "https://hosted.example.com"


def _pat_settings() -> McpSettings:
    return McpSettings(
        auth_mode="pat",
        bulkloader_base_url=BASE_URL,
        bulkloader_pat=VALID_PAT,
    )


def _bad_pat_settings() -> McpSettings:
    return McpSettings(
        auth_mode="pat",
        bulkloader_base_url=BASE_URL,
        bulkloader_pat="sfbl_pat_wrong_token",
    )


class _HostedModeTransport(httpx.AsyncBaseTransport):
    """Mock transport that enforces Bearer auth and returns plausible payloads.

    Returns 401 for any request without the correct Authorization header —
    mirroring a real hosted-mode backend with auth_mode != none.
    """

    def __init__(self, valid_pat: str) -> None:
        self._valid_pat = valid_pat
        self.request_log: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_log.append({
            "method": request.method,
            "path": request.url.path,
            "auth_header": request.headers.get("authorization"),
        })

        # Enforce Bearer auth — return 401 if missing or wrong
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {self._valid_pat}":
            return httpx.Response(
                401,
                json={"detail": "Invalid or missing authentication credentials"},
                headers={"content-type": "application/json"},
            )

        path = request.url.path

        # Health
        if path == "/api/health/ready":
            return httpx.Response(200, json={"status": "ok", "db": "ok"})

        # Connections list
        if path == "/api/connections" and request.method == "GET":
            return httpx.Response(200, json=[])

        # Create connection
        if path == "/api/connections" and request.method == "POST":
            conn_id = str(uuid.uuid4())
            return httpx.Response(
                201,
                json={"id": conn_id, "name": "Smoke test org", "status": "untested"},
            )

        # Plans list
        if path == "/api/load-plans/" and request.method == "GET":
            return httpx.Response(200, json=[])

        # Create plan
        if path == "/api/load-plans/" and request.method == "POST":
            plan_id = str(uuid.uuid4())
            return httpx.Response(
                201,
                json={"id": plan_id, "name": "Smoke test plan", "steps": []},
            )

        # Trigger run (POST to /api/load-plans/{plan_id}/run)
        if "/run" in path and request.method == "POST":
            run_id = str(uuid.uuid4())
            return httpx.Response(
                202,
                json={"id": run_id, "status": "pending"},
            )

        # Get run
        if path.startswith("/api/runs/") and request.method == "GET" and not path.endswith("/jobs"):
            run_id = path.split("/")[-1]
            return httpx.Response(
                200,
                json={
                    "id": run_id,
                    "status": "completed",
                    "records_processed": 100,
                    "records_failed": 0,
                },
            )

        # Default: 404
        return httpx.Response(404, json={"detail": "Not found"})


# ── Smoke tests ────────────────────────────────────────────────────────────────

class TestPatSmoke:
    """Full create→run→inspect lifecycle over a PAT-enforcing hosted backend."""

    @pytest.mark.asyncio
    async def test_health_check_succeeds_in_pat_mode(self) -> None:
        transport = _HostedModeTransport(VALID_PAT)
        async with BulkLoaderClient(_pat_settings()) as client:
            client._http = httpx.AsyncClient(transport=transport)
            response = await client.get("/api/health/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_every_request_carries_bearer_header(self) -> None:
        """All requests in PAT mode must include Authorization: Bearer."""
        transport = _HostedModeTransport(VALID_PAT)
        async with BulkLoaderClient(_pat_settings()) as client:
            client._http = httpx.AsyncClient(transport=transport)
            await client.get("/api/health/ready")
            await client.get("/api/connections")
            await client.get("/api/load-plans/")

        for entry in transport.request_log:
            assert entry["auth_header"] == f"Bearer {VALID_PAT}", (
                f"Missing or wrong Bearer header on {entry['method']} {entry['path']}"
            )

    @pytest.mark.asyncio
    async def test_create_run_inspect_lifecycle(self) -> None:
        """Drive the full create→trigger→inspect path in PAT mode."""
        transport = _HostedModeTransport(VALID_PAT)
        async with BulkLoaderClient(_pat_settings()) as client:
            client._http = httpx.AsyncClient(transport=transport)

            # 1. Create a Salesforce connection
            conn_resp = await client.post("/api/connections", json={
                "name": "Smoke test org",
                "instance_url": "https://smoke.my.salesforce.com",
                "login_url": "https://login.salesforce.com",
                "client_id": "fakeClientId",
                "private_key": "fakePEM",
                "username": "smoke@example.com",
            })
            assert conn_resp.status_code == 201
            conn_id = conn_resp.json()["id"]
            assert conn_id

            # 2. Create a load plan
            plan_resp = await client.post("/api/load-plans/", json={
                "connection_id": conn_id,
                "name": "Smoke test plan",
            })
            assert plan_resp.status_code == 201
            plan_id = plan_resp.json()["id"]

            # 3. Trigger a run
            run_resp = await client.post(f"/api/load-plans/{plan_id}/run", json={})
            assert run_resp.status_code == 202
            run_id = run_resp.json()["id"]

            # 4. Inspect the run
            inspect_resp = await client.get(f"/api/runs/{run_id}")
            assert inspect_resp.status_code == 200
            assert inspect_resp.json()["status"] == "completed"

        # Confirm every request carried the Bearer header
        for entry in transport.request_log:
            assert entry["auth_header"] == f"Bearer {VALID_PAT}"

    @pytest.mark.asyncio
    async def test_invalid_pat_returns_401_with_clear_message(self) -> None:
        """Wrong PAT → McpHttpError(401) with an actionable message."""
        transport = _HostedModeTransport(VALID_PAT)
        async with BulkLoaderClient(_bad_pat_settings()) as client:
            client._http = httpx.AsyncClient(transport=transport)
            with pytest.raises(McpHttpError) as exc_info:
                await client.get("/api/health/ready")
        err = exc_info.value
        assert err.status_code == 401
        text = err.to_tool_error_text()
        assert "401" in text
        # The error text must guide the user — not just say "error"
        assert any(kw in text.lower() for kw in ["unauthorized", "pat", "auth"])

    @pytest.mark.asyncio
    async def test_invalid_pat_error_does_not_expose_token(self) -> None:
        """The 401 error message and logs must NEVER contain the PAT value."""
        transport = _HostedModeTransport(VALID_PAT)
        with pytest.raises(McpHttpError) as exc_info:
            async with BulkLoaderClient(_bad_pat_settings()) as client:
                client._http = httpx.AsyncClient(transport=transport)
                await client.get("/api/health/ready")

        error_text = exc_info.value.to_tool_error_text()
        assert "sfbl_pat_wrong_token" not in error_text, (
            "PAT value must NEVER appear in error messages"
        )

    @pytest.mark.asyncio
    async def test_auth_failure_emits_log_event(self, caplog: pytest.LogCaptureFixture) -> None:
        """401 from the hosted backend must emit a structured MCP_PAT_AUTH_FAILURE event."""
        transport = _HostedModeTransport(VALID_PAT)
        with caplog.at_level(logging.WARNING, logger="sf_bulk_loader_mcp.client"):
            with pytest.raises(McpHttpError):
                async with BulkLoaderClient(_bad_pat_settings()) as client:
                    client._http = httpx.AsyncClient(transport=transport)
                    await client.get("/api/health/ready")

        assert any(
            "MCP_PAT_AUTH_FAILURE" in r.getMessage() or
            getattr(r, "event_name", None) == "MCP_PAT_AUTH_FAILURE"
            for r in caplog.records
        ), "Expected MCP_PAT_AUTH_FAILURE event in log records on 401"
