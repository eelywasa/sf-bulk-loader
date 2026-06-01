# MCP Tool-Binding Contract

This document is the authoritative reference for how Salesforce Bulk Loader MCP
tools are generated, what they cover, and where auth/base-URL injection happens.

## Two categories of tools

### 1. Schema-visible tools (generated / curated)

Tools in this category are built **only** from routes that appear in
`GET /openapi.json` — i.e. routes defined WITHOUT `include_in_schema=False`.
The curated list below is the exhaustive set; we do not expose the entire
OpenAPI surface to reduce noise for MCP callers.

**Implementation home:** `server.py` → `CURATED_TOOLS` constant + `call_tool`
dispatcher.  SFBL-360..363 fill these in.

### 2. Hand-written tools

Any endpoint tagged `include_in_schema=False` on the backend side NEVER appears
in `/openapi.json` and therefore CANNOT be generated.  These tools are written
by hand and must be kept in sync with the backend manually.

Current hand-written tools:

| Tool name | Endpoint                | Reason excluded from schema           |
|-----------|-------------------------|---------------------------------------|
| `health`  | `GET /api/health/ready` | `include_in_schema=False` in utility.py |

See `tools/health.py` for the implementation.

## Auth and base-URL injection

**Rule:** Auth headers and the base URL are injected by `client.py` and `config.py`
**only**.  They are NEVER read from OpenAPI `servers` or `security` fields.

- `config.py` owns the auth-mode enum and URL/PAT settings.
- `client.py` resolves the base URL (explicit override → discovery file) and
  injects `Authorization: Bearer` when `AUTH_MODE=pat`.
- Tool implementations call `client.get/post/put/delete` — they never build
  their own base URLs or set auth headers directly.

## Curated tool list (SFBL-360..363)

The table below tracks all curated tools.  Input schemas are hand-written to
match the backend request/response shapes in `backend/app/schemas/` (connection.py
and input_connection.py).  Secrets are never echoed — the API's own response
serialisers omit them, and the tool formatters only pass response payloads
through.

### Implemented

| Tool name                 | Endpoint                                     | Ticket    | Status      |
|---------------------------|----------------------------------------------|-----------|-------------|
| `health`                  | `GET /api/health/ready`                      | SFBL-359  | Implemented |
| `list_connections`        | `GET /api/connections`                       | SFBL-360  | Implemented |
| `get_connection`          | `GET /api/connections/{id}`                  | SFBL-360  | Implemented |
| `create_connection`       | `POST /api/connections`                      | SFBL-360  | Implemented |
| `update_connection`       | `PUT /api/connections/{id}`                  | SFBL-360  | Implemented |
| `test_connection`         | `POST /api/connections/{id}/test`            | SFBL-360  | Implemented |
| `list_sobjects`           | `GET /api/connections/{id}/objects`          | SFBL-360  | Implemented |
| `list_input_connections`  | `GET /api/input-connections`                 | SFBL-360  | Implemented |
| `get_input_connection`    | `GET /api/input-connections/{id}`            | SFBL-360  | Implemented |
| `create_input_connection` | `POST /api/input-connections`                | SFBL-360  | Implemented |
| `update_input_connection` | `PUT /api/input-connections/{id}`            | SFBL-360  | Implemented |
| `test_input_connection`   | `POST /api/input-connections/{id}/test`      | SFBL-360  | Implemented |

### TODO

| Tool name     | Endpoint                    | Ticket    | Status |
|---------------|-----------------------------|-----------|--------|
| `list_plans`  | `GET /api/plans`            | SFBL-361  | TODO   |
| `get_plan`    | `GET /api/plans/{id}`       | SFBL-361  | TODO   |
| `create_plan` | `POST /api/plans`           | SFBL-361  | TODO   |
| `update_plan` | `PUT /api/plans/{id}`       | SFBL-361  | TODO   |
| `list_runs`   | `GET /api/runs`             | SFBL-362  | TODO   |
| `get_run`     | `GET /api/runs/{id}`        | SFBL-362  | TODO   |
| `trigger_run` | `POST /api/runs`            | SFBL-362  | TODO   |
| `abort_run`   | `POST /api/runs/{id}/abort` | SFBL-362  | TODO   |
| `list_jobs`   | `GET /api/jobs`             | SFBL-363  | TODO   |
| `get_job`     | `GET /api/jobs/{id}`        | SFBL-363  | TODO   |

## Error handling contract

All HTTP errors from the backend are mapped to `McpHttpError` in `client.py`
before being returned to the MCP caller.  **Raw stack traces are never
surfaced.**  The safe representation is `McpHttpError.to_tool_error_text()`,
which includes:

- The HTTP status code.
- A human-readable message for the status class.
- The backend `detail` field if the response was JSON (FastAPI convention).

Tool implementations must catch `McpHttpError` and return it as a
`TextContent` error string — see `server.py → _handle_health` for the pattern.
