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
from mcp.types import ToolAnnotations

from .tools.health import check_health, format_health_result
from .tools import connections as _conn
from .tools import plans as _plans
from .tools import runs as _runs
from .tools import results as _results


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
    # NOTE: real prefix is /api/load-plans (NOT /api/plans as the 359 placeholder guessed)
    {"name": "list_plans",         "endpoint": "GET /api/load-plans/",                             "status": "implemented"},
    {"name": "get_plan",           "endpoint": "GET /api/load-plans/{plan_id}",                    "status": "implemented"},
    {"name": "create_plan",        "endpoint": "POST /api/load-plans/",                            "status": "implemented"},
    {"name": "update_plan",        "endpoint": "PUT /api/load-plans/{plan_id}",                    "status": "implemented"},
    {"name": "delete_plan",        "endpoint": "DELETE /api/load-plans/{plan_id}",                 "status": "implemented"},
    {"name": "duplicate_plan",     "endpoint": "POST /api/load-plans/{plan_id}/duplicate",         "status": "implemented"},

    # ── Load Steps (SFBL-361) ────────────────────────────────────────────────
    {"name": "add_step",           "endpoint": "POST /api/load-plans/{plan_id}/steps",             "status": "implemented"},
    {"name": "update_step",        "endpoint": "PUT /api/load-plans/{plan_id}/steps/{step_id}",    "status": "implemented"},
    {"name": "delete_step",        "endpoint": "DELETE /api/load-plans/{plan_id}/steps/{step_id}", "status": "implemented"},
    {"name": "reorder_steps",      "endpoint": "POST /api/load-plans/{plan_id}/steps/reorder",     "status": "implemented"},

    # ── Validation helpers (SFBL-361) ────────────────────────────────────────
    {"name": "validate_soql",      "endpoint": "POST /api/load-plans/{plan_id}/validate-soql",           "status": "implemented"},
    {"name": "preview_step",       "endpoint": "POST /api/load-plans/{plan_id}/steps/{step_id}/preview", "status": "implemented"},

    # ── Load Runs (SFBL-362) ────────────────────────────────────────────────────
    # NOTE: real prefix is /api/runs (NOT /api/load-runs as the 359 placeholder guessed).
    # trigger_run lives on the load-PLANS router (POST /api/load-plans/{plan_id}/run),
    # not on the load-runs router — verified from backend/app/api/load_plans.py.
    {"name": "trigger_run",        "endpoint": "POST /api/load-plans/{plan_id}/run",             "status": "implemented"},
    {"name": "list_runs",          "endpoint": "GET /api/runs/",                                 "status": "implemented"},
    {"name": "get_run",            "endpoint": "GET /api/runs/{run_id}",                         "status": "implemented"},
    {"name": "abort_run",          "endpoint": "POST /api/runs/{run_id}/abort",                  "status": "implemented"},
    {"name": "retry_step",         "endpoint": "POST /api/runs/{run_id}/retry-step/{step_id}",   "status": "implemented"},
    {"name": "list_jobs",          "endpoint": "GET /api/runs/{run_id}/jobs",                    "status": "implemented"},
    {"name": "get_job",            "endpoint": "GET /api/jobs/{job_id}",                         "status": "implemented"},

    # ── Job Results / failure inspection (SFBL-363) ──────────────────────────
    {"name": "preview_success_csv",     "endpoint": "GET /api/jobs/{job_id}/success-csv/preview",     "status": "implemented"},
    {"name": "preview_error_csv",       "endpoint": "GET /api/jobs/{job_id}/error-csv/preview",       "status": "implemented"},
    {"name": "preview_unprocessed_csv", "endpoint": "GET /api/jobs/{job_id}/unprocessed-csv/preview", "status": "implemented"},
    {"name": "download_logs_zip",       "endpoint": "GET /api/runs/{run_id}/logs.zip",                "status": "implemented"},
    {"name": "failure_summary",         "endpoint": "GET /api/jobs/{job_id}/error-csv/preview (aggregated)", "status": "implemented"},

    # ── Health (hand-written — see below) ────────────────────────────────────
    # Not in this list; registered separately because it targets a
    # schema-excluded endpoint.
]


# ── Tool registration ─────────────────────────────────────────────────────────

def _register_tools(server: Server, client: BulkLoaderClient, settings: McpSettings | None = None) -> None:
    """Register all MCP tools on *server*.

    ``settings`` is passed through to handlers that need non-request config
    (e.g. download_logs_zip uses ``settings.bulkloader_app_name`` to resolve
    the OS-convention save directory for the logs ZIP).

    Each tool calls ``client.get/post/put/delete`` so auth headers and base URL
    are injected centrally.
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

            # ── Load Plans (SFBL-361) ─────────────────────────────────────────
            Tool(
                name="list_plans",
                description=(
                    "List all load plans. Returns plan summaries including connection, "
                    "output_connection_id, and step counts."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            ),
            Tool(
                name="get_plan",
                description=(
                    "Get a load plan by ID, including its full list of ordered steps. "
                    "Shows output_connection_id (output S3 sink), error thresholds, "
                    "and all step input/output chaining."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string", "description": "UUID of the load plan."},
                    },
                    "required": ["plan_id"],
                },
            ),
            Tool(
                name="create_plan",
                description=(
                    "Create a new load plan. "
                    "output_connection_id (optional) must reference a storage connection "
                    "with direction 'out' or 'both' — the backend validates this."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "connection_id": {"type": "string", "description": "UUID of the Salesforce connection to use."},
                        "name": {"type": "string", "description": "Human-readable plan name."},
                        "description": {"type": "string", "description": "Optional free-text description."},
                        "output_connection_id": {
                            "type": "string",
                            "description": (
                                "UUID of a storage connection used to store query-step output. "
                                "Must have direction 'out' or 'both'. "
                                "Backend returns 422 if the direction is wrong."
                            ),
                        },
                        "abort_on_step_failure": {"type": "boolean", "default": True, "description": "Abort the run if a step exceeds the error threshold."},
                        "error_threshold_pct": {"type": "number", "default": 10.0, "description": "Percentage of records that may fail before the step is considered failed."},
                        "max_parallel_jobs": {"type": "integer", "default": 5, "description": "Maximum Bulk API jobs to run concurrently."},
                        "consecutive_failure_threshold": {"type": "integer", "default": 5, "description": "Circuit-breaker: number of consecutive job failures before the run is aborted."},
                    },
                    "required": ["connection_id", "name"],
                },
            ),
            Tool(
                name="update_plan",
                description=(
                    "Update an existing load plan. All fields are optional; only supplied "
                    "fields are changed. output_connection_id must reference a storage "
                    "connection with direction 'out' or 'both' — the backend validates this."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string", "description": "UUID of the load plan to update."},
                        "connection_id": {"type": "string"},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "output_connection_id": {
                            "type": "string",
                            "description": (
                                "UUID of a storage connection to use as output sink. "
                                "Must have direction 'out' or 'both'. "
                                "Backend returns 422 if the direction is wrong."
                            ),
                        },
                        "abort_on_step_failure": {"type": "boolean"},
                        "error_threshold_pct": {"type": "number"},
                        "max_parallel_jobs": {"type": "integer"},
                        "consecutive_failure_threshold": {"type": "integer"},
                    },
                    "required": ["plan_id"],
                },
            ),
            Tool(
                name="delete_plan",
                description=(
                    "Delete a load plan by ID. This also deletes all associated steps. "
                    "Requires plans.manage permission."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string", "description": "UUID of the load plan to delete."},
                    },
                    "required": ["plan_id"],
                },
            ),
            Tool(
                name="duplicate_plan",
                description=(
                    "Create a copy of an existing load plan including all its steps. "
                    "The copy is returned as the new plan."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string", "description": "UUID of the load plan to duplicate."},
                    },
                    "required": ["plan_id"],
                },
            ),

            # ── Load Steps (SFBL-361) ──────────────────────────────────────────
            Tool(
                name="add_step",
                description=(
                    "Add a new step to a load plan. "
                    "For DML operations (insert/update/upsert/delete): provide csv_file_pattern, "
                    "OR set input_from_step_id to chain this step from an upstream query step's output. "
                    "input_from_step_id is mutually exclusive with csv_file_pattern and input_connection_id. "
                    "For query/queryAll operations: provide soql. "
                    "Backend returns 422 with a clear message for invalid combinations."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string", "description": "UUID of the load plan to add the step to."},
                        "object_name": {"type": "string", "description": "Salesforce SObject API name (e.g. Account)."},
                        "operation": {
                            "type": "string",
                            "enum": ["insert", "update", "upsert", "delete", "query", "queryAll"],
                            "description": "Bulk API 2.0 operation.",
                        },
                        "sequence": {"type": "integer", "description": "Step sequence order. Auto-assigned if omitted."},
                        "name": {"type": "string", "description": "Optional human-readable step name (unique within plan)."},
                        "csv_file_pattern": {"type": "string", "description": "Glob pattern for CSV files (DML ops only). Mutually exclusive with input_from_step_id."},
                        "soql": {"type": "string", "description": "SOQL query string (query/queryAll ops only)."},
                        "external_id_field": {"type": "string", "description": "External ID field name for upsert operations."},
                        "partition_size": {"type": "integer", "description": "Row count per Bulk API job partition."},
                        "assignment_rule_id": {"type": "string", "description": "Salesforce assignment rule ID."},
                        "input_connection_id": {"type": "string", "description": "UUID of storage connection to read CSVs from. Mutually exclusive with input_from_step_id."},
                        "input_from_step_id": {
                            "type": "string",
                            "description": (
                                "UUID of an upstream query/queryAll step whose output CSV feeds this DML step. "
                                "Mutually exclusive with csv_file_pattern and input_connection_id. "
                                "The upstream step must have a lower sequence number. "
                                "Backend returns 422 if the reference is invalid."
                            ),
                        },
                    },
                    "required": ["plan_id", "object_name", "operation"],
                },
            ),
            Tool(
                name="update_step",
                description=(
                    "Update an existing step within a load plan. All fields are optional. "
                    "input_from_step_id can be set to chain this step from an upstream query step's output; "
                    "it is mutually exclusive with csv_file_pattern and input_connection_id. "
                    "Backend validates the merged effective state and returns 422 with a clear message for invalid combinations."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string", "description": "UUID of the load plan."},
                        "step_id": {"type": "string", "description": "UUID of the step to update."},
                        "object_name": {"type": "string"},
                        "operation": {
                            "type": "string",
                            "enum": ["insert", "update", "upsert", "delete", "query", "queryAll"],
                        },
                        "sequence": {"type": "integer"},
                        "name": {"type": "string"},
                        "csv_file_pattern": {"type": "string"},
                        "soql": {"type": "string"},
                        "external_id_field": {"type": "string"},
                        "partition_size": {"type": "integer"},
                        "assignment_rule_id": {"type": "string"},
                        "input_connection_id": {"type": "string"},
                        "input_from_step_id": {
                            "type": "string",
                            "description": (
                                "UUID of an upstream query step whose output feeds this step. "
                                "Mutually exclusive with csv_file_pattern / input_connection_id."
                            ),
                        },
                    },
                    "required": ["plan_id", "step_id"],
                },
            ),
            Tool(
                name="delete_step",
                description=(
                    "Delete a step from a load plan. "
                    "Backend returns 422 if any downstream step depends on this step via input_from_step_id — "
                    "clear or repoint those downstream steps first."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string", "description": "UUID of the load plan."},
                        "step_id": {"type": "string", "description": "UUID of the step to delete."},
                    },
                    "required": ["plan_id", "step_id"],
                },
            ),
            Tool(
                name="reorder_steps",
                description=(
                    "Reassign step sequences to match the provided ordered list of step IDs. "
                    "The list must contain all step IDs in the plan. "
                    "Returns the reordered steps with updated sequence numbers."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string", "description": "UUID of the load plan."},
                        "step_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Ordered list of all step UUIDs; the first is sequence 1.",
                        },
                    },
                    "required": ["plan_id", "step_ids"],
                },
            ),

            # ── Validation helpers (SFBL-361) ──────────────────────────────────
            Tool(
                name="validate_soql",
                description=(
                    "Validate an ad-hoc SOQL string against the plan's Salesforce connection "
                    "using the Bulk API explain endpoint. The SOQL does not need to be saved on "
                    "a step — pass it directly for live validation in the step editor."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string", "description": "UUID of the load plan (determines which Salesforce connection is used)."},
                        "soql": {"type": "string", "description": "The SOQL query string to validate."},
                    },
                    "required": ["plan_id", "soql"],
                },
            ),
            Tool(
                name="preview_step",
                description=(
                    "Preview a step's input: for DML steps, discovers CSV files matching the "
                    "step's pattern and returns row counts. For query steps, validates the SOQL "
                    "via the Salesforce explain endpoint. For steps with input_from_step_id, "
                    "returns a note that the input is resolved at run time."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string", "description": "UUID of the load plan."},
                        "step_id": {"type": "string", "description": "UUID of the step to preview."},
                    },
                    "required": ["plan_id", "step_id"],
                },
            ),

            # ── Load Runs (SFBL-362) ──────────────────────────────────────────
            #
            # DESTRUCTIVE-ACTION SAFETY
            # trigger_run, abort_run, and retry_step execute real Bulk API DML
            # or abort live Salesforce jobs.  They each carry:
            #   - ToolAnnotations(destructiveHint=True) — signals to MCP clients.
            #   - A required `confirm: true` boolean in inputSchema — the handler
            #     refuses with a structured message if confirm is absent or False,
            #     and makes NO backend call.
            #
            # MONITORING (REST polling, NOT WebSocket)
            # Use get_run / list_jobs for single-shot status reads and poll at your
            # own cadence.  The WebSocket at /ws/runs/{run_id} is intentionally NOT
            # used here — it relies on a separate short-lived token (validate_ws_token)
            # that bypasses get_current_user and is out of scope for the MCP channel.
            #
            # Recommended poll cadence:
            #   - Poll get_run every 5–10 s; apply exponential backoff up to 60 s cap.
            #   - Terminal statuses: "completed", "failed", "aborted".
            #   - Hard timeout: ~30 min for production loads (adjust for data volume).
            #
            # OBSERVABILITY
            # MCP adds NO new run/job instrumentation.  All lifecycle events are
            # already emitted by the backend orchestrator (events.py RunEvent /
            # JobEvent).  Duplicating them here would cause double-counting.
            Tool(
                name="trigger_run",
                description=(
                    "Trigger a new load run for a plan.  "
                    "Creates a LoadRun record and enqueues the plan for background "
                    "execution by the backend orchestrator.  Returns the new run with "
                    "its ID and initial 'pending' status.  "
                    "DESTRUCTIVE: executes real Bulk API DML against Salesforce.  "
                    "Pass confirm=true to proceed.  "
                    "Monitor progress with get_run (poll every 5–10 s; terminal "
                    "statuses: completed/failed/aborted)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {
                            "type": "string",
                            "description": "UUID of the load plan to execute.",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "Must be true to proceed.  "
                                "This tool triggers real Salesforce Bulk API DML.  "
                                "Pass confirm=true to confirm you intend to run it."
                            ),
                        },
                    },
                    "required": ["plan_id", "confirm"],
                },
                annotations=ToolAnnotations(destructiveHint=True),
            ),
            Tool(
                name="list_runs",
                description=(
                    "List load run history, optionally filtered by plan, status, or "
                    "start-time range.  Returns run summaries (id, status, record counts, "
                    "timestamps).  Use get_run to fetch full detail including per-job breakdown."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {
                            "type": "string",
                            "description": "Filter by load plan UUID.",
                        },
                        "run_status": {
                            "type": "string",
                            "enum": ["pending", "running", "completed", "failed", "aborted"],
                            "description": "Filter by run status.",
                        },
                        "started_after": {
                            "type": "string",
                            "description": "ISO-8601 datetime — only runs started after this time.",
                        },
                        "started_before": {
                            "type": "string",
                            "description": "ISO-8601 datetime — only runs started before this time.",
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="get_run",
                description=(
                    "Get full details for a single load run, including status, record counts, "
                    "error summary (auth_error / storage_error / circuit_breaker / "
                    "preflight_warnings), and the per-job breakdown.  "
                    "Use this to poll for run completion: terminal statuses are "
                    "'completed', 'failed', 'aborted'.  "
                    "Recommended poll cadence: 5–10 s with exponential backoff up to 60 s."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "UUID of the load run.",
                        },
                    },
                    "required": ["run_id"],
                },
            ),
            Tool(
                name="abort_run",
                description=(
                    "Abort a pending or running load.  In-progress Bulk API jobs are "
                    "marked aborted on the Salesforce side.  Returns the updated run.  "
                    "DESTRUCTIVE: aborts live Salesforce Bulk API jobs.  "
                    "Pass confirm=true to proceed."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "UUID of the load run to abort.",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "Must be true to proceed.  "
                                "This tool aborts a live Salesforce Bulk API run.  "
                                "Pass confirm=true to confirm you intend to abort it."
                            ),
                        },
                    },
                    "required": ["run_id", "confirm"],
                },
                annotations=ToolAnnotations(destructiveHint=True),
            ),
            Tool(
                name="retry_step",
                description=(
                    "Create a new load run that retries only the failed or aborted jobs "
                    "of a single step in an existing run.  Returns the new retry run.  "
                    "DESTRUCTIVE: re-submits Bulk API DML for failed records.  "
                    "Pass confirm=true to proceed."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "UUID of the original load run.",
                        },
                        "step_id": {
                            "type": "string",
                            "description": "UUID of the load step to retry.",
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": (
                                "Must be true to proceed.  "
                                "This tool re-submits Bulk API DML for failed records.  "
                                "Pass confirm=true to confirm you intend to retry it."
                            ),
                        },
                    },
                    "required": ["run_id", "step_id", "confirm"],
                },
                annotations=ToolAnnotations(destructiveHint=True),
            ),
            Tool(
                name="list_jobs",
                description=(
                    "List the individual Bulk API job partitions for a load run, "
                    "optionally filtered by step or job status.  Returns partition index, "
                    "status, record counts (processed/failed/successful), and any "
                    "error messages.  Use this for granular per-partition monitoring."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "UUID of the load run.",
                        },
                        "step_id": {
                            "type": "string",
                            "description": "Filter to jobs for a specific load step UUID.",
                        },
                        "job_status": {
                            "type": "string",
                            "enum": ["pending", "running", "completed", "failed", "aborted"],
                            "description": "Filter by job status.",
                        },
                    },
                    "required": ["run_id"],
                },
            ),
            Tool(
                name="get_job",
                description=(
                    "Get full details for a single Bulk API job partition, including "
                    "record counts (processed/failed/successful), Salesforce job ID, "
                    "instance URL, error message, result file paths, and timestamps."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "UUID of the job record.",
                        },
                    },
                    "required": ["job_id"],
                },
            ),

            # ── Job Results / failure inspection (SFBL-363) ─────────────────────
            #
            # ROW-LIMIT AND CELL-BYTE CAPS
            # Default row limit: 50 (matches backend default).
            # Hard max row limit: 500 (mirrors backend ``le=500`` Query constraint).
            # Cell-byte cap: 100 KB — rows are truncated beyond this, with a
            # "__truncated__" marker row appended to the result.
            #
            # AUTH NOTE
            # All preview and download endpoints are gated by ``files.view_contents``.
            # In desktop profile (auth_mode=none) the virtual admin holds all
            # permissions, so no auth header is required.
            Tool(
                name="preview_success_csv",
                description=(
                    "Preview the success result CSV rows for a completed Bulk API job.  "
                    "Returns paginated rows, column headers, and pagination metadata.  "
                    "Use offset/limit for pagination; use columns to restrict which fields "
                    "are returned.  "
                    "Default row limit: 50; hard max: 500.  "
                    "Total cell bytes are capped at 100 KB regardless of row count — "
                    "a '__truncated__' marker row is appended if the cap is reached.  "
                    "Requires files.view_contents permission (auto-granted in desktop mode)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "UUID of the job record.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": f"Maximum rows to return (default {_results.DEFAULT_ROW_LIMIT}, max {_results.MAX_ROW_LIMIT}).",
                            "default": _results.DEFAULT_ROW_LIMIT,
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Number of rows to skip before returning results (for pagination).",
                            "default": 0,
                        },
                        "columns": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of column names to include in the output.  All columns returned if omitted.",
                        },
                        "filters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "column": {"type": "string"},
                                    "value": {"type": "string"},
                                },
                                "required": ["column", "value"],
                            },
                            "description": "Optional row filters — each item matches rows where the named column equals the value.",
                        },
                    },
                    "required": ["job_id"],
                },
            ),
            Tool(
                name="preview_error_csv",
                description=(
                    "Preview the error result CSV rows for a completed Bulk API job.  "
                    "Error rows include the Salesforce error message in the 'sf__Error' column.  "
                    "Returns paginated rows, column headers, and pagination metadata.  "
                    "Default row limit: 50; hard max: 500.  "
                    "Total cell bytes are capped at 100 KB — a '__truncated__' marker row is "
                    "appended if the cap is reached.  "
                    "Use failure_summary to aggregate error reasons across multiple jobs.  "
                    "Requires files.view_contents permission (auto-granted in desktop mode)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "UUID of the job record.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": f"Maximum rows to return (default {_results.DEFAULT_ROW_LIMIT}, max {_results.MAX_ROW_LIMIT}).",
                            "default": _results.DEFAULT_ROW_LIMIT,
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Number of rows to skip before returning results (for pagination).",
                            "default": 0,
                        },
                        "columns": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of column names to include in the output.",
                        },
                        "filters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "column": {"type": "string"},
                                    "value": {"type": "string"},
                                },
                                "required": ["column", "value"],
                            },
                            "description": "Optional row filters.",
                        },
                    },
                    "required": ["job_id"],
                },
            ),
            Tool(
                name="preview_unprocessed_csv",
                description=(
                    "Preview the unprocessed records CSV for a completed Bulk API job.  "
                    "Unprocessed records are those that were not attempted (e.g. the job was "
                    "aborted before they could be submitted).  "
                    "Default row limit: 50; hard max: 500.  "
                    "Total cell bytes are capped at 100 KB — a '__truncated__' marker row is "
                    "appended if the cap is reached.  "
                    "Requires files.view_contents permission (auto-granted in desktop mode)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "UUID of the job record.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": f"Maximum rows to return (default {_results.DEFAULT_ROW_LIMIT}, max {_results.MAX_ROW_LIMIT}).",
                            "default": _results.DEFAULT_ROW_LIMIT,
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Number of rows to skip before returning results (for pagination).",
                            "default": 0,
                        },
                        "columns": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of column names to include in the output.",
                        },
                        "filters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "column": {"type": "string"},
                                    "value": {"type": "string"},
                                },
                                "required": ["column", "value"],
                            },
                            "description": "Optional row filters.",
                        },
                    },
                    "required": ["job_id"],
                },
            ),
            Tool(
                name="download_logs_zip",
                description=(
                    "Download the complete logs ZIP for a load run and save it to disk.  "
                    "The ZIP contains the success, error, and/or unprocessed result CSVs for "
                    "every Bulk API job partition in the run, organised as "
                    "'{step_id}/partition_{n}_{type}.csv' inside the archive.  "
                    "Because ZIP bytes cannot be inlined for an LLM, the file is saved to "
                    "the OS-convention user data directory and the saved path plus an archive "
                    "member listing (name + size) are returned.  "
                    "Repeated calls for the same run_id overwrite the previous download.  "
                    "Requires files.view_contents permission (auto-granted in desktop mode)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "UUID of the load run.",
                        },
                        "success": {
                            "type": "boolean",
                            "description": "Include success CSVs (default true).",
                            "default": True,
                        },
                        "errors": {
                            "type": "boolean",
                            "description": "Include error CSVs (default true).",
                            "default": True,
                        },
                        "unprocessed": {
                            "type": "boolean",
                            "description": "Include unprocessed CSVs (default true).",
                            "default": True,
                        },
                    },
                    "required": ["run_id"],
                },
            ),
            Tool(
                name="failure_summary",
                description=(
                    "Aggregate the common failure reasons across one or more failed jobs.  "
                    "For each job, fetches error rows from the error result CSV and groups "
                    "them by the Salesforce error message ('sf__Error' column).  "
                    "Returns the top error reasons ordered by frequency, a total error row "
                    "count, and a list of jobs whose error CSV was unavailable.  "
                    "This is the primary tool for explaining *why* records failed — use it "
                    "after a run completes with errors to give the operator actionable insight.  "
                    "Requires files.view_contents permission (auto-granted in desktop mode)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "job_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of job UUIDs to analyse.  Typically the failed/error jobs from a run (use list_jobs with job_status='failed' to get them).",
                        },
                        "top_n": {
                            "type": "integer",
                            "description": "Maximum number of distinct error reasons to return (default 20).",
                            "default": 20,
                        },
                    },
                    "required": ["job_ids"],
                },
            ),
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

        # ── Load Plans (SFBL-361) ──────────────────────────────────────────────
        if name == "list_plans":
            return await _handle_list_plans(client)
        if name == "get_plan":
            return await _handle_get_plan(client, arguments)
        if name == "create_plan":
            return await _handle_create_plan(client, arguments)
        if name == "update_plan":
            return await _handle_update_plan(client, arguments)
        if name == "delete_plan":
            return await _handle_delete_plan(client, arguments)
        if name == "duplicate_plan":
            return await _handle_duplicate_plan(client, arguments)

        # ── Load Steps (SFBL-361) ──────────────────────────────────────────────
        if name == "add_step":
            return await _handle_add_step(client, arguments)
        if name == "update_step":
            return await _handle_update_step(client, arguments)
        if name == "delete_step":
            return await _handle_delete_step(client, arguments)
        if name == "reorder_steps":
            return await _handle_reorder_steps(client, arguments)

        # ── Validation helpers (SFBL-361) ──────────────────────────────────────
        if name == "validate_soql":
            return await _handle_validate_soql(client, arguments)
        if name == "preview_step":
            return await _handle_preview_step(client, arguments)

        # ── Load Runs (SFBL-362) ──────────────────────────────────────────────
        if name == "trigger_run":
            return await _handle_trigger_run(client, arguments)
        if name == "list_runs":
            return await _handle_list_runs(client, arguments)
        if name == "get_run":
            return await _handle_get_run(client, arguments)
        if name == "abort_run":
            return await _handle_abort_run(client, arguments)
        if name == "retry_step":
            return await _handle_retry_step(client, arguments)
        if name == "list_jobs":
            return await _handle_list_jobs(client, arguments)
        if name == "get_job":
            return await _handle_get_job(client, arguments)

        # ── Job Results / failure inspection (SFBL-363) ───────────────────────
        if name == "preview_success_csv":
            return await _handle_preview_success_csv(client, arguments)
        if name == "preview_error_csv":
            return await _handle_preview_error_csv(client, arguments)
        if name == "preview_unprocessed_csv":
            return await _handle_preview_unprocessed_csv(client, arguments)
        if name == "download_logs_zip":
            return await _handle_download_logs_zip(client, arguments, settings)
        if name == "failure_summary":
            return await _handle_failure_summary(client, arguments)

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


# ── Plan tool handlers ────────────────────────────────────────────────────────
# Each handler wraps the corresponding call helper in tools/plans.py,
# catching McpHttpError (structured) and bare Exception (network / unexpected)
# so no raw stack traces are ever returned to MCP callers.


def _plan_error(exc: McpHttpError) -> list[TextContent]:
    return _safe_text(f"Plan error: {exc.to_tool_error_text()}")


def _step_error(exc: McpHttpError) -> list[TextContent]:
    return _safe_text(f"Step error: {exc.to_tool_error_text()}")


def _validation_error(exc: McpHttpError) -> list[TextContent]:
    return _safe_text(f"Validation error: {exc.to_tool_error_text()}")


async def _handle_list_plans(client: BulkLoaderClient) -> list[TextContent]:
    try:
        payload = await _plans.list_plans(client)
        return _safe_text(_plans.format_list_plans(payload))
    except McpHttpError as exc:
        return _plan_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_get_plan(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _plans.get_plan(client, args["plan_id"])
        return _safe_text(_plans.format_plan(payload))
    except McpHttpError as exc:
        return _plan_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_create_plan(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _plans.create_plan(client, args)
        return _safe_text(_plans.format_create_plan(payload))
    except McpHttpError as exc:
        return _plan_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_update_plan(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    plan_id = args.pop("plan_id")
    try:
        payload = await _plans.update_plan(client, plan_id, args)
        return _safe_text(_plans.format_update_plan(payload))
    except McpHttpError as exc:
        return _plan_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_delete_plan(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        await _plans.delete_plan(client, args["plan_id"])
        return _safe_text(_plans.format_delete_plan(args["plan_id"]))
    except McpHttpError as exc:
        return _plan_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_duplicate_plan(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _plans.duplicate_plan(client, args["plan_id"])
        return _safe_text(_plans.format_duplicate_plan(payload))
    except McpHttpError as exc:
        return _plan_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


# ── Step tool handlers ────────────────────────────────────────────────────────


async def _handle_add_step(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    plan_id = args.pop("plan_id")
    try:
        payload = await _plans.add_step(client, plan_id, args)
        return _safe_text(_plans.format_add_step(payload))
    except McpHttpError as exc:
        return _step_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_update_step(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    plan_id = args.pop("plan_id")
    step_id = args.pop("step_id")
    try:
        payload = await _plans.update_step(client, plan_id, step_id, args)
        return _safe_text(_plans.format_update_step(payload))
    except McpHttpError as exc:
        return _step_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_delete_step(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        await _plans.delete_step(client, args["plan_id"], args["step_id"])
        return _safe_text(_plans.format_delete_step(args["step_id"]))
    except McpHttpError as exc:
        return _step_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_reorder_steps(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _plans.reorder_steps(client, args["plan_id"], args["step_ids"])
        return _safe_text(_plans.format_reorder_steps(payload))
    except McpHttpError as exc:
        return _step_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


# ── Validation helper handlers ────────────────────────────────────────────────


async def _handle_validate_soql(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _plans.validate_soql(client, args["plan_id"], args["soql"])
        return _safe_text(_plans.format_validate_soql(payload))
    except McpHttpError as exc:
        return _validation_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_preview_step(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _plans.preview_step(client, args["plan_id"], args["step_id"])
        return _safe_text(_plans.format_preview_step(payload))
    except McpHttpError as exc:
        return _validation_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


# ── Run tool handlers ─────────────────────────────────────────────────────────
# All handlers follow the same pattern as plan/connection handlers above:
#   1. Extract typed arguments.
#   2. For destructive tools, call _runs.check_confirm() FIRST — return the
#      refusal string immediately if confirm is absent/False (NO backend call).
#   3. Call the corresponding helper in tools/runs.py.
#   4. Format and return as TextContent.
#   5. Catch McpHttpError (structured) and bare Exception (network/unexpected)
#      so no raw stack traces are ever returned to MCP callers.


def _run_error(exc: McpHttpError) -> list[TextContent]:
    return _safe_text(f"Run error: {exc.to_tool_error_text()}")


async def _handle_trigger_run(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    refusal = _runs.check_confirm(args)
    if refusal is not None:
        return _safe_text(refusal)
    try:
        payload = await _runs.trigger_run(client, args["plan_id"])
        return _safe_text(_runs.format_trigger_run(payload))
    except McpHttpError as exc:
        return _run_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_list_runs(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _runs.list_runs(
            client,
            plan_id=args.get("plan_id"),
            run_status=args.get("run_status"),
            started_after=args.get("started_after"),
            started_before=args.get("started_before"),
        )
        return _safe_text(_runs.format_list_runs(payload))
    except McpHttpError as exc:
        return _run_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_get_run(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _runs.get_run(client, args["run_id"])
        return _safe_text(_runs.format_run(payload, include_jobs=True))
    except McpHttpError as exc:
        return _run_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_abort_run(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    refusal = _runs.check_confirm(args)
    if refusal is not None:
        return _safe_text(refusal)
    try:
        payload = await _runs.abort_run(client, args["run_id"])
        return _safe_text(_runs.format_abort_run(payload))
    except McpHttpError as exc:
        return _run_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_retry_step(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    refusal = _runs.check_confirm(args)
    if refusal is not None:
        return _safe_text(refusal)
    try:
        payload = await _runs.retry_step(client, args["run_id"], args["step_id"])
        return _safe_text(_runs.format_retry_step(payload))
    except McpHttpError as exc:
        return _run_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_list_jobs(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _runs.list_jobs(
            client,
            args["run_id"],
            step_id=args.get("step_id"),
            job_status=args.get("job_status"),
        )
        return _safe_text(_runs.format_list_jobs(payload))
    except McpHttpError as exc:
        return _run_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_get_job(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _runs.get_job(client, args["job_id"])
        return _safe_text(_runs.format_job(payload))
    except McpHttpError as exc:
        return _run_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


# ── Result / failure-inspection handlers (SFBL-363) ──────────────────────────
# Each handler follows the same error-isolation pattern as the run handlers above.


def _results_error(exc: McpHttpError) -> list[TextContent]:
    return _safe_text(f"Results error: {exc.to_tool_error_text()}")


async def _handle_preview_success_csv(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    job_id = args["job_id"]
    try:
        payload = await _results.preview_success_csv(
            client,
            job_id,
            limit=args.get("limit"),
            offset=args.get("offset", 0),
            columns=args.get("columns"),
            filters=args.get("filters"),
        )
        return _safe_text(_results.format_preview(payload, "success", job_id))
    except McpHttpError as exc:
        return _results_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_preview_error_csv(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    job_id = args["job_id"]
    try:
        payload = await _results.preview_error_csv(
            client,
            job_id,
            limit=args.get("limit"),
            offset=args.get("offset", 0),
            columns=args.get("columns"),
            filters=args.get("filters"),
        )
        return _safe_text(_results.format_preview(payload, "error", job_id))
    except McpHttpError as exc:
        return _results_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_preview_unprocessed_csv(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    job_id = args["job_id"]
    try:
        payload = await _results.preview_unprocessed_csv(
            client,
            job_id,
            limit=args.get("limit"),
            offset=args.get("offset", 0),
            columns=args.get("columns"),
            filters=args.get("filters"),
        )
        return _safe_text(_results.format_preview(payload, "unprocessed", job_id))
    except McpHttpError as exc:
        return _results_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_download_logs_zip(
    client: BulkLoaderClient,
    args: dict[str, Any],
    settings: McpSettings | None,
) -> list[TextContent]:
    run_id = args["run_id"]
    app_name = (settings.bulkloader_app_name if settings else "Salesforce Bulk Loader")
    try:
        payload = await _results.download_logs_zip(
            client,
            run_id,
            success=args.get("success", True),
            errors=args.get("errors", True),
            unprocessed=args.get("unprocessed", True),
            app_name=app_name,
        )
        return _safe_text(_results.format_download_logs_zip(payload, run_id))
    except McpHttpError as exc:
        return _results_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


async def _handle_failure_summary(
    client: BulkLoaderClient, args: dict[str, Any]
) -> list[TextContent]:
    try:
        payload = await _results.failure_summary(
            client,
            args["job_ids"],
            top_n=args.get("top_n", 20),
        )
        return _safe_text(_results.format_failure_summary(payload))
    except McpHttpError as exc:
        return _results_error(exc)
    except Exception as exc:
        return _unexpected_error(exc)


# ── Server entrypoint ─────────────────────────────────────────────────────────

async def _run_server() -> None:
    settings = McpSettings()
    server = Server("sf-bulk-loader-mcp")

    async with BulkLoaderClient(settings) as client:
        _register_tools(server, client, settings)

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
