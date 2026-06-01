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
from .tools import connections as _conn


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
    # ── Salesforce Connections (SFBL-360) ────────────────────────────────────
    {"name": "list_connections",    "endpoint": "GET /api/connections",                         "status": "implemented"},
    {"name": "get_connection",      "endpoint": "GET /api/connections/{id}",                    "status": "implemented"},
    {"name": "create_connection",   "endpoint": "POST /api/connections",                        "status": "implemented"},
    {"name": "update_connection",   "endpoint": "PUT /api/connections/{id}",                    "status": "implemented"},
    {"name": "test_connection",     "endpoint": "POST /api/connections/{id}/test",              "status": "implemented"},
    {"name": "list_sobjects",       "endpoint": "GET /api/connections/{id}/objects",            "status": "implemented"},

    # ── Storage (input) Connections (SFBL-360) ───────────────────────────────
    {"name": "list_input_connections",   "endpoint": "GET /api/input-connections",              "status": "implemented"},
    {"name": "get_input_connection",     "endpoint": "GET /api/input-connections/{id}",         "status": "implemented"},
    {"name": "create_input_connection",  "endpoint": "POST /api/input-connections",             "status": "implemented"},
    {"name": "update_input_connection",  "endpoint": "PUT /api/input-connections/{id}",         "status": "implemented"},
    {"name": "test_input_connection",    "endpoint": "POST /api/input-connections/{id}/test",   "status": "implemented"},

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

            # ── Salesforce connections (SFBL-360) ─────────────────────────────
            Tool(
                name="list_connections",
                description=(
                    "List all Salesforce org connections configured in the Bulk Loader. "
                    "Returns public fields only (no credential secrets)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            ),
            Tool(
                name="get_connection",
                description=(
                    "Get details for a single Salesforce connection by ID. "
                    "Returns credential metadata (client_id, username, etc.) if the "
                    "caller holds the view_credentials permission; otherwise returns "
                    "public fields only."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "connection_id": {
                            "type": "string",
                            "description": "UUID of the Salesforce connection.",
                        },
                    },
                    "required": ["connection_id"],
                },
            ),
            Tool(
                name="create_connection",
                description=(
                    "Create a new Salesforce org connection. Requires connections.manage "
                    "permission. The private_key (PEM) is encrypted at rest; it is "
                    "never returned by subsequent read operations."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Human-readable connection name."},
                        "instance_url": {"type": "string", "description": "Salesforce instance URL (e.g. https://myorg.my.salesforce.com)."},
                        "login_url": {"type": "string", "description": "OAuth login URL (e.g. https://login.salesforce.com)."},
                        "client_id": {"type": "string", "description": "Connected-app Consumer Key."},
                        "private_key": {"type": "string", "description": "RSA private key in PEM format."},
                        "username": {"type": "string", "description": "Salesforce username for JWT bearer flow."},
                        "is_sandbox": {"type": "boolean", "description": "Set true for sandbox orgs.", "default": False},
                    },
                    "required": ["name", "instance_url", "login_url", "client_id", "private_key", "username"],
                },
            ),
            Tool(
                name="update_connection",
                description=(
                    "Update an existing Salesforce connection. All fields are optional; "
                    "only supplied fields are changed. Requires connections.manage permission."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "connection_id": {"type": "string", "description": "UUID of the connection to update."},
                        "name": {"type": "string"},
                        "instance_url": {"type": "string"},
                        "login_url": {"type": "string"},
                        "client_id": {"type": "string"},
                        "private_key": {"type": "string", "description": "Replacement PEM private key."},
                        "username": {"type": "string"},
                        "is_sandbox": {"type": "boolean"},
                    },
                    "required": ["connection_id"],
                },
            ),
            Tool(
                name="test_connection",
                description=(
                    "Test a Salesforce connection by attempting JWT authentication and a "
                    "lightweight Salesforce API call. Returns success/failure and a message."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "connection_id": {"type": "string", "description": "UUID of the connection to test."},
                    },
                    "required": ["connection_id"],
                },
            ),
            Tool(
                name="list_sobjects",
                description=(
                    "List the SObject API names available as load targets for a Salesforce "
                    "connection. Returns objects that are createable, updateable, or deletable."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "connection_id": {"type": "string", "description": "UUID of the Salesforce connection."},
                    },
                    "required": ["connection_id"],
                },
            ),

            # ── Storage (input) connections (SFBL-360) ────────────────────────
            Tool(
                name="list_input_connections",
                description=(
                    "List all storage (S3) input/output connections. "
                    "Credential fields are never returned."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": ["in", "out", "both"],
                            "description": (
                                "Optional filter: 'in' returns in+both; "
                                "'out' returns out+both; 'both' matches exactly."
                            ),
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="get_input_connection",
                description="Get details for a single storage (S3) connection by ID. Credential fields are never returned.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "ic_id": {"type": "string", "description": "UUID of the storage connection."},
                    },
                    "required": ["ic_id"],
                },
            ),
            Tool(
                name="create_input_connection",
                description=(
                    "Create a new S3 storage connection. The access_key_id, "
                    "secret_access_key, and optional session_token are encrypted at rest "
                    "and are NEVER returned in responses."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Human-readable connection name."},
                        "provider": {"type": "string", "description": "Storage provider. Currently 's3'."},
                        "bucket": {"type": "string", "description": "S3 bucket name."},
                        "root_prefix": {"type": "string", "description": "Optional key prefix within the bucket."},
                        "region": {"type": "string", "description": "AWS region (e.g. us-east-1)."},
                        "direction": {
                            "type": "string",
                            "enum": ["in", "out", "both"],
                            "description": "Whether this connection is used for input, output, or both.",
                            "default": "in",
                        },
                        "access_key_id": {"type": "string", "description": "AWS access key ID."},
                        "secret_access_key": {"type": "string", "description": "AWS secret access key."},
                        "session_token": {"type": "string", "description": "Optional AWS session token."},
                    },
                    "required": ["name", "provider", "bucket", "access_key_id", "secret_access_key"],
                },
            ),
            Tool(
                name="update_input_connection",
                description=(
                    "Update an existing S3 storage connection. All fields are optional. "
                    "Credential fields (access_key_id, secret_access_key) are re-encrypted "
                    "if provided and never returned in responses."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "ic_id": {"type": "string", "description": "UUID of the storage connection to update."},
                        "name": {"type": "string"},
                        "bucket": {"type": "string"},
                        "root_prefix": {"type": "string"},
                        "region": {"type": "string"},
                        "access_key_id": {"type": "string"},
                        "secret_access_key": {"type": "string"},
                        "session_token": {"type": "string"},
                        "direction": {"type": "string", "enum": ["in", "out", "both"]},
                    },
                    "required": ["ic_id"],
                },
            ),
            Tool(
                name="test_input_connection",
                description=(
                    "Test an S3 storage connection. Verifies read access (and write access "
                    "for 'out'/'both' connections). Returns success/failure and a message."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "ic_id": {"type": "string", "description": "UUID of the storage connection to test."},
                    },
                    "required": ["ic_id"],
                },
            ),

            # TODO (SFBL-361..363): add curated tools here as they are implemented.
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name == "health":
            return await _handle_health(client)

        # ── Salesforce connections (SFBL-360) ──────────────────────────────────
        if name == "list_connections":
            return await _handle_list_connections(client)
        if name == "get_connection":
            return await _handle_get_connection(client, arguments)
        if name == "create_connection":
            return await _handle_create_connection(client, arguments)
        if name == "update_connection":
            return await _handle_update_connection(client, arguments)
        if name == "test_connection":
            return await _handle_test_connection(client, arguments)
        if name == "list_sobjects":
            return await _handle_list_sobjects(client, arguments)

        # ── Storage (input) connections (SFBL-360) ─────────────────────────────
        if name == "list_input_connections":
            return await _handle_list_input_connections(client, arguments)
        if name == "get_input_connection":
            return await _handle_get_input_connection(client, arguments)
        if name == "create_input_connection":
            return await _handle_create_input_connection(client, arguments)
        if name == "update_input_connection":
            return await _handle_update_input_connection(client, arguments)
        if name == "test_input_connection":
            return await _handle_test_input_connection(client, arguments)

        # TODO (SFBL-361..363): dispatch curated tools here.
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


# ── Connection tool handlers ──────────────────────────────────────────────────
# Each handler wraps the corresponding call helper in tools/connections.py,
# catching McpHttpError (structured) and bare Exception (network / unexpected)
# so no raw stack traces are ever returned to MCP callers.

def _safe_text(text: str) -> list[TextContent]:
    return [TextContent(type="text", text=text)]


def _connection_error(exc: McpHttpError) -> list[TextContent]:
    return _safe_text(f"Connection error: {exc.to_tool_error_text()}")


def _unexpected_error(exc: Exception) -> list[TextContent]:
    return _safe_text(f"Unexpected error: {type(exc).__name__}: {exc}")


async def _handle_list_connections(client: BulkLoaderClient) -> list[TextContent]:
    try:
        payload = await _conn.list_connections(client)
        return _safe_text(_conn.format_list_connections(payload))
    except McpHttpError as exc:
        return _connection_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_get_connection(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _conn.get_connection(client, args["connection_id"])
        return _safe_text(_conn.format_connection(payload))
    except McpHttpError as exc:
        return _connection_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_create_connection(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    # Never echo the private_key back — strip it from the formatted output
    # (the API response already omits it; the args are only used for the POST body).
    try:
        payload = await _conn.create_connection(client, args)
        return _safe_text(_conn.format_create_connection(payload))
    except McpHttpError as exc:
        return _connection_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_update_connection(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    connection_id = args.pop("connection_id")
    try:
        payload = await _conn.update_connection(client, connection_id, args)
        return _safe_text(_conn.format_update_connection(payload))
    except McpHttpError as exc:
        return _connection_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_test_connection(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _conn.check_connection(client, args["connection_id"])
        return _safe_text(_conn.format_test_connection(payload))
    except McpHttpError as exc:
        return _connection_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_list_sobjects(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    connection_id = args["connection_id"]
    try:
        payload = await _conn.list_sobjects(client, connection_id)
        return _safe_text(_conn.format_list_sobjects(connection_id, payload))
    except McpHttpError as exc:
        return _connection_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


# ── Storage (input) connection handlers ───────────────────────────────────────


def _input_connection_error(exc: McpHttpError) -> list[TextContent]:
    return _safe_text(f"Storage connection error: {exc.to_tool_error_text()}")


async def _handle_list_input_connections(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    direction = args.get("direction")
    try:
        payload = await _conn.list_input_connections(client, direction=direction)
        return _safe_text(_conn.format_list_input_connections(payload))
    except McpHttpError as exc:
        return _input_connection_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_get_input_connection(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _conn.get_input_connection(client, args["ic_id"])
        return _safe_text(_conn.format_input_connection(payload))
    except McpHttpError as exc:
        return _input_connection_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_create_input_connection(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    # Credential fields (access_key_id, secret_access_key, session_token) are
    # in the POST body but NEVER echoed back — the API response omits them.
    try:
        payload = await _conn.create_input_connection(client, args)
        return _safe_text(_conn.format_create_input_connection(payload))
    except McpHttpError as exc:
        return _input_connection_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_update_input_connection(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    ic_id = args.pop("ic_id")
    try:
        payload = await _conn.update_input_connection(client, ic_id, args)
        return _safe_text(_conn.format_update_input_connection(payload))
    except McpHttpError as exc:
        return _input_connection_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_test_input_connection(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _conn.check_input_connection(client, args["ic_id"])
        return _safe_text(_conn.format_test_input_connection(payload))
    except McpHttpError as exc:
        return _input_connection_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


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
