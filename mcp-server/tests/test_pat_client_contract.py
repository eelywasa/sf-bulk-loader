"""PAT-mode CLIENT CONTRACT tests (SFBL-371/373).

Scope — what these tests DO prove (client-side only, no backend):
  1. The client injects ``Authorization: Bearer <pat>`` on every verb.
  2. A 401 from the server is mapped to a clear ``McpHttpError`` and emits the
     ``MCP_PAT_AUTH_FAILURE`` event.
  3. The PAT value never leaks into error messages or log records.

Scope — what these tests deliberately do NOT prove:
  These use a stub ``httpx`` transport that checks the Bearer header against a
  constant. They do NOT exercise the backend's real PAT verification (HMAC hash,
  DB lookup, expiry/revocation) and the response shapes here are fabricated.
  The REAL end-to-end assurance — a PAT minted via ``POST /api/me/tokens`` against
  a hosted-mode backend, then driven through the MCP lifecycle — lives in
  ``backend/tests/test_mcp_pat_integration.py``. Keep that distinction: this file
  is a fast, dependency-free contract check that runs in the mcp-server CI job.
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
    """Stub transport that checks the Bearer header against a constant.

    NOT a backend: it returns 401 for any request whose Authorization header
    doesn't equal ``Bearer <valid_pat>``, and returns fabricated payloads for
    the happy path. This exercises only the CLIENT's header/error behaviour —
    real PAT verification is covered by backend/tests/test_mcp_pat_integration.py.
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


# ── Client contract tests ──────────────────────────────────────────────────────

class TestPatClientContract:
    """Client-side header/error/logging contract in PAT mode (mock transport).

    These assert what the CLIENT does; they do not validate any backend. The
    real PAT lifecycle is in backend/tests/test_mcp_pat_integration.py.
    """

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
    async def test_client_attaches_bearer_across_verbs(self) -> None:
        """The client attaches the Bearer header on GET/POST across a sequence of
        calls. NOTE: this is a header-propagation check against a stub — it does
        NOT prove the backend accepts the token or that these routes exist with
        these shapes (see test_mcp_pat_integration.py for that)."""
        transport = _HostedModeTransport(VALID_PAT)
        async with BulkLoaderClient(_pat_settings()) as client:
            client._http = httpx.AsyncClient(transport=transport)
            conn_resp = await client.post("/api/connections", json={
                "name": "Stub org",
                "instance_url": "https://stub.my.salesforce.com",
                "login_url": "https://login.salesforce.com",
                "client_id": "fakeClientId",
                "private_key": "fakePEM",
                "username": "stub@example.com",
            })
            conn_id = conn_resp.json()["id"]
            plan_resp = await client.post("/api/load-plans/", json={
                "connection_id": conn_id, "name": "Stub plan",
            })
            plan_id = plan_resp.json()["id"]
            run_resp = await client.post(f"/api/load-plans/{plan_id}/run", json={})
            run_id = run_resp.json()["id"]
            await client.get(f"/api/runs/{run_id}")

        # The contract under test: every verb carried the Bearer header.
        assert transport.request_log, "no requests were recorded"
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
    async def test_invalid_pat_error_does_not_expose_token(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The 401 error message AND all log records must NEVER contain the PAT.

        Inspects both the rendered error text and the full log records (message
        + every record attribute incl. the ``extra`` dict), so a leak via a log
        attribute would be caught here too — not just in the message string."""
        transport = _HostedModeTransport(VALID_PAT)
        with caplog.at_level(logging.WARNING, logger="sf_bulk_loader_mcp.client"):
            with pytest.raises(McpHttpError) as exc_info:
                async with BulkLoaderClient(_bad_pat_settings()) as client:
                    client._http = httpx.AsyncClient(transport=transport)
                    await client.get("/api/health/ready")

        error_text = exc_info.value.to_tool_error_text()
        assert "sfbl_pat_wrong_token" not in error_text, (
            "PAT value must NEVER appear in error messages"
        )
        log_blob = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
        assert "sfbl_pat_wrong_token" not in log_blob, (
            "PAT value must NEVER appear in any log record (incl. extra dict)"
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
