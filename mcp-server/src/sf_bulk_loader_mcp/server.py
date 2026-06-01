"""MCP server entrypoint — stdio transport.

Registers all tools and starts the server.  Tools are divided into two
categories (see docs/tool-binding.md for the full contract):

  1. Hand-written tools — endpoints excluded from /openapi.json (health, etc.).
  2. Generated / curated tools — built from /openapi.json schema-visible routes.
     These are TODO placeholders until SFBL-360..363 are implemented.

Usage (stdio, for Claude Desktop / other MCP clients):
    python3 -m sf_bulk_loader_mcp.server
    # or, via the console entrypoint:
    sf-bulk-loader-mcp
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import mcp.server.stdio
from mcp.server import Server
from mcp.types import CallToolResult, TextContent, Tool

from .client import BulkLoaderClient, McpHttpError
from .config import McpSettings
from .tools.health import check_health, format_health_result


# ── Curated tool list ─────────────────────────────────────────────────────────
#
# This list documents the intended ~12–15 lifecycle tools for SFBL-360..363.
# Each entry maps a tool name to the backend endpoint it will call.  Entries
# marked TODO are not yet implemented; they will be wired up in SFBL-360..363.
#
# When a tool is implemented, remove its TODO marker and register it below in
# ``_register_tools()``.
#
# NOTE: these tools are built ONLY from schema-visible routes (/openapi.json).
# Excluded endpoints (health, metrics, …) are covered by hand-written tools.
#
CURATED_TOOLS: list[dict[str, str]] = [
    # ── Connections (SFBL-360) ───────────────────────────────────────────────
    {"name": "list_connections",   "endpoint": "GET /api/connections",          "status": "TODO"},
    {"name": "get_connection",     "endpoint": "GET /api/connections/{id}",     "status": "TODO"},
    {"name": "create_connection",  "endpoint": "POST /api/connections",         "status": "TODO"},
    {"name": "test_connection",    "endpoint": "POST /api/connections/{id}/test", "status": "TODO"},

    # ── Load Plans (SFBL-361) ────────────────────────────────────────────────
    {"name": "list_plans",         "endpoint": "GET /api/plans",                "status": "TODO"},
    {"name": "get_plan",           "endpoint": "GET /api/plans/{id}",           "status": "TODO"},
    {"name": "create_plan",        "endpoint": "POST /api/plans",               "status": "TODO"},
    {"name": "update_plan",        "endpoint": "PUT /api/plans/{id}",           "status": "TODO"},

    # ── Load Runs (SFBL-362) ─────────────────────────────────────────────────
    {"name": "list_runs",          "endpoint": "GET /api/runs",                 "status": "TODO"},
    {"name": "get_run",            "endpoint": "GET /api/runs/{id}",            "status": "TODO"},
    {"name": "trigger_run",        "endpoint": "POST /api/runs",                "status": "TODO"},
    {"name": "abort_run",          "endpoint": "POST /api/runs/{id}/abort",     "status": "TODO"},

    # ── Job Results (SFBL-363) ───────────────────────────────────────────────
    {"name": "list_jobs",          "endpoint": "GET /api/jobs",                 "status": "TODO"},
    {"name": "get_job",            "endpoint": "GET /api/jobs/{id}",            "status": "TODO"},

    # ── Health (hand-written — see below) ────────────────────────────────────
    # Not in this list; registered separately because it targets a
    # schema-excluded endpoint.
]


# ── Tool registration ─────────────────────────────────────────────────────────

def _register_tools(server: Server, client: BulkLoaderClient) -> None:
    """Register all MCP tools on *server*.

    Currently registers:
      - ``health``  (hand-written)

    Generated / curated tools will be registered here as SFBL-360..363 are
    implemented.  Each tool must call ``client.get/post/put/delete`` so that
    auth headers and base URL are injected centrally.
    """

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="health",
                description=(
                    "Check whether the Salesforce Bulk Loader backend is ready "
                    "to accept requests.  Returns readiness status and any "
                    "diagnostic details."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            ),
            # TODO (SFBL-360..363): add curated tools here as they are implemented.
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name == "health":
            return await _handle_health(client)

        # TODO (SFBL-360..363): dispatch curated tools here.
        return [TextContent(type="text", text=f"Tool '{name}' is not yet implemented.")]


async def _handle_health(client: BulkLoaderClient) -> list[TextContent]:
    """Handle the ``health`` tool call."""
    try:
        payload = await check_health(client)
        text = format_health_result(payload)
    except McpHttpError as exc:
        text = f"Health check failed: {exc.to_tool_error_text()}"
    except Exception as exc:
        # Network errors etc. — do NOT leak tracebacks.
        text = f"Health check unavailable: {type(exc).__name__}: {exc}"
    return [TextContent(type="text", text=text)]


# ── Server entrypoint ─────────────────────────────────────────────────────────

async def _run_server() -> None:
    settings = McpSettings()
    server = Server("sf-bulk-loader-mcp")

    async with BulkLoaderClient(settings) as client:
        _register_tools(server, client)

        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )


def main() -> None:
    """Console-script entrypoint (sf-bulk-loader-mcp)."""
    asyncio.run(_run_server())


if __name__ == "__main__":
    main()
