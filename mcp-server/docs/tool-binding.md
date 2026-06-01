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

### Load Plans and Steps (SFBL-361)

| Tool name         | Endpoint                                                         | Ticket   | Status      |
|-------------------|------------------------------------------------------------------|----------|-------------|
| `list_plans`      | `GET /api/load-plans/`                                           | SFBL-361 | Implemented |
| `get_plan`        | `GET /api/load-plans/{plan_id}`                                  | SFBL-361 | Implemented |
| `create_plan`     | `POST /api/load-plans/`                                          | SFBL-361 | Implemented |
| `update_plan`     | `PUT /api/load-plans/{plan_id}`                                  | SFBL-361 | Implemented |
| `delete_plan`     | `DELETE /api/load-plans/{plan_id}`                               | SFBL-361 | Implemented |
| `duplicate_plan`  | `POST /api/load-plans/{plan_id}/duplicate`                       | SFBL-361 | Implemented |
| `add_step`        | `POST /api/load-plans/{plan_id}/steps`                           | SFBL-361 | Implemented |
| `update_step`     | `PUT /api/load-plans/{plan_id}/steps/{step_id}`                  | SFBL-361 | Implemented |
| `delete_step`     | `DELETE /api/load-plans/{plan_id}/steps/{step_id}`               | SFBL-361 | Implemented |
| `reorder_steps`   | `POST /api/load-plans/{plan_id}/steps/reorder`                   | SFBL-361 | Implemented |
| `validate_soql`   | `POST /api/load-plans/{plan_id}/validate-soql`                   | SFBL-361 | Implemented |
| `preview_step`    | `POST /api/load-plans/{plan_id}/steps/{step_id}/preview`         | SFBL-361 | Implemented |

### Load Runs and Jobs (SFBL-362)

**Note on endpoint prefixes:**
- `trigger_run` lives on the **load-plans** router (prefix `/api/load-plans`), NOT the load-runs router.
  Verified from `backend/app/api/load_plans.py` at the `start_load_run` route handler.
- All other run endpoints use the **load-runs** router (prefix `/api/runs`).
  Verified from `backend/app/api/load_runs.py`.
- Job list endpoint (`/api/runs/{run_id}/jobs`) and job detail endpoint (`/api/jobs/{job_id}`)
  are both in the **jobs** router, which has NO prefix — the full path is in the route decorator.
  Verified from `backend/app/api/jobs.py`.

| Tool name      | Endpoint                                              | Ticket   | Status      | Destructive? |
|----------------|-------------------------------------------------------|----------|-------------|--------------|
| `trigger_run`  | `POST /api/load-plans/{plan_id}/run`                  | SFBL-362 | Implemented | Yes          |
| `list_runs`    | `GET /api/runs/`                                      | SFBL-362 | Implemented | No           |
| `get_run`      | `GET /api/runs/{run_id}`                              | SFBL-362 | Implemented | No           |
| `abort_run`    | `POST /api/runs/{run_id}/abort`                       | SFBL-362 | Implemented | Yes          |
| `retry_step`   | `POST /api/runs/{run_id}/retry-step/{step_id}`        | SFBL-362 | Implemented | Yes          |
| `list_jobs`    | `GET /api/runs/{run_id}/jobs`                         | SFBL-362 | Implemented | No           |
| `get_job`      | `GET /api/jobs/{job_id}`                              | SFBL-362 | Implemented | No           |

### Job result-file inspection (SFBL-363)

| Tool name | Endpoint | Ticket | Status |
|-----------|----------|--------|--------|
| *(result-file download and preview tools)* | — | SFBL-363 | TODO |

## Destructive-action safety (trigger_run, abort_run, retry_step)

These three tools each carry two safety mechanisms:

1. **`ToolAnnotations(destructiveHint=True)`** — set on the `Tool` definition so
   MCP clients that inspect tool metadata can surface a confirmation UX.

2. **`confirm: true` required in inputSchema** — the handler calls
   `_runs.check_confirm(args)` as its *first* action, before making any HTTP
   request.  If `confirm` is absent or `False`, it returns a structured refusal
   message and makes **no backend call**.

The refusal message is:
> "This tool performs a destructive action (real Bulk API DML or live-run mutation).
>  You must pass confirm=true to proceed.  Re-call this tool with the same
>  arguments plus `confirm=true` to execute."

## Monitoring: REST polling cadence (no WebSocket)

All run/job tools are single-shot status reads.  The WebSocket at
`/ws/runs/{run_id}` is intentionally NOT used: the `validate_ws_token` path
bypasses `get_current_user` (it uses a dedicated short-lived token) and is out
of scope for the MCP channel.

### Recommended polling loop for agents waiting on terminal status

```
run_id = trigger_run(plan_id=..., confirm=True)["id"]
interval = 5          # seconds — start short
max_interval = 60     # seconds — cap
deadline = now() + 1800  # 30-minute hard timeout
terminal = {"completed", "failed", "aborted"}

while now() < deadline:
    run = get_run(run_id)
    if run["status"] in terminal:
        break
    sleep(interval)
    interval = min(interval * 2, max_interval)  # exponential backoff
```

- **Initial interval:** 5 s (matches the backend's `SF_POLL_INTERVAL_INITIAL`).
- **Max interval:** 60 s (double the backend's `SF_POLL_INTERVAL_MAX` — the
  backend polls internally, so there is no need to poll faster than its own
  cycle).
- **Hard timeout:** 30 minutes is a safe default for most loads; adjust
  upward for multi-million-record plans.
- **Terminal statuses:** `completed`, `failed`, `aborted`.
- **Per-job detail:** call `list_jobs(run_id)` on each poll iteration if
  you need per-partition error messages (e.g. to surface which step failed).

## Observability

MCP adds NO new run/job instrumentation.  All lifecycle events
(`RunEvent.STARTED`, `RunEvent.COMPLETED`, `JobEvent.CREATED`, etc.) are
already emitted by the backend orchestrator via `app/observability/events.py`.
Duplicating them in the MCP layer would cause double-counting in any metrics
pipeline.  See `docs/observability.md` for the full event catalogue.

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
