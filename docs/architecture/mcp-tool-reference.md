# MCP tool reference

All 36 tools exposed by the Salesforce Bulk Loader MCP server, grouped by
area. Source of truth: `CURATED_TOOLS` in
`mcp-server/src/sf_bulk_loader_mcp/server.py` plus the hand-written `health`
tool in `mcp-server/src/sf_bulk_loader_mcp/tools/health.py`.

For the binding contract (auth injection, destructive-action safety, error
handling) see [`mcp-server/docs/tool-binding.md`](../../mcp-server/docs/tool-binding.md).

---

## Health (hand-written)

The `health` tool targets `GET /api/health/ready`, which is excluded from the
OpenAPI schema (`include_in_schema=False` in `backend/app/api/utility.py`) and
therefore cannot be generated. It is the only hand-written tool.

| Tool | Endpoint | Destructive? |
|---|---|---|
| `health` | `GET /api/health/ready` | No |

---

## Salesforce connections

Tools for managing Salesforce org credentials (JWT Bearer flow). Private keys
are encrypted at rest and never returned by read operations.

| Tool | Endpoint | Destructive? |
|---|---|---|
| `list_connections` | `GET /api/connections` | No |
| `get_connection` | `GET /api/connections/{id}` | No |
| `create_connection` | `POST /api/connections` | No |
| `update_connection` | `PUT /api/connections/{id}` | No |
| `test_connection` | `POST /api/connections/{id}/test` | No |
| `list_sobjects` | `GET /api/connections/{id}/objects` | No |

---

## Storage (S3 input/output) connections

Tools for managing S3 buckets used as input sources or output sinks. AWS
credentials are encrypted at rest and never returned.

| Tool | Endpoint | Destructive? |
|---|---|---|
| `list_input_connections` | `GET /api/input-connections` | No |
| `get_input_connection` | `GET /api/input-connections/{id}` | No |
| `create_input_connection` | `POST /api/input-connections` | No |
| `update_input_connection` | `PUT /api/input-connections/{id}` | No |
| `test_input_connection` | `POST /api/input-connections/{id}/test` | No |

---

## Load plans and steps

Tools for authoring and managing load plans and their ordered steps.

| Tool | Endpoint | Destructive? |
|---|---|---|
| `list_plans` | `GET /api/load-plans/` | No |
| `get_plan` | `GET /api/load-plans/{plan_id}` | No |
| `create_plan` | `POST /api/load-plans/` | No |
| `update_plan` | `PUT /api/load-plans/{plan_id}` | No |
| `delete_plan` | `DELETE /api/load-plans/{plan_id}` | No |
| `duplicate_plan` | `POST /api/load-plans/{plan_id}/duplicate` | No |
| `add_step` | `POST /api/load-plans/{plan_id}/steps` | No |
| `update_step` | `PUT /api/load-plans/{plan_id}/steps/{step_id}` | No |
| `delete_step` | `DELETE /api/load-plans/{plan_id}/steps/{step_id}` | No |
| `reorder_steps` | `POST /api/load-plans/{plan_id}/steps/reorder` | No |

---

## Validation helpers

Tools for validating plan configuration before triggering a run.

| Tool | Endpoint | Destructive? |
|---|---|---|
| `validate_soql` | `POST /api/load-plans/{plan_id}/validate-soql` | No |
| `preview_step` | `POST /api/load-plans/{plan_id}/steps/{step_id}/preview` | No |

---

## Load runs and jobs

Tools for executing and monitoring runs. Three tools are destructive and
require `confirm=true`.

> **Destructive-action safety:** `trigger_run`, `abort_run`, and `retry_step`
> each carry `ToolAnnotations(destructiveHint=True)` and enforce a required
> `confirm: true` parameter. The handler calls `check_confirm(args)` before
> making any backend call; if `confirm` is absent or `False` it returns a
> structured refusal with no side effects.

| Tool | Endpoint | Destructive? |
|---|---|---|
| `trigger_run` | `POST /api/load-plans/{plan_id}/run` | **Yes** |
| `list_runs` | `GET /api/runs/` | No |
| `get_run` | `GET /api/runs/{run_id}` | No |
| `abort_run` | `POST /api/runs/{run_id}/abort` | **Yes** |
| `retry_step` | `POST /api/runs/{run_id}/retry-step/{step_id}` | **Yes** |
| `list_jobs` | `GET /api/runs/{run_id}/jobs` | No |
| `get_job` | `GET /api/jobs/{job_id}` | No |

### Polling cadence

`get_run` and `list_jobs` are single-shot status reads. There is no WebSocket
support in the MCP channel. Recommended polling loop:

```
interval = 5          # start short (matches backend SF_POLL_INTERVAL_INITIAL)
max_interval = 60     # cap
deadline = now() + 1800  # 30-minute hard timeout
terminal = {"completed", "completed_with_errors", "failed", "aborted"}

while now() < deadline:
    run = get_run(run_id)
    if run["status"] in terminal:
        break
    sleep(interval)
    interval = min(interval * 2, max_interval)
```

---

## Job results and failure inspection

Tools for reading result CSVs and diagnosing failures. All require
`files.view_contents` permission (auto-granted in desktop profile).

Preview tools support `limit`/`offset` pagination (default 50 rows, max 500)
and optional `columns`/`filters` narrowing. Cell bytes are capped at 100 KB
per call; a `__truncated__` marker row is appended when the cap is reached.

| Tool | Endpoint | Destructive? |
|---|---|---|
| `preview_success_csv` | `GET /api/jobs/{job_id}/success-csv/preview` | No |
| `preview_error_csv` | `GET /api/jobs/{job_id}/error-csv/preview` | No |
| `preview_unprocessed_csv` | `GET /api/jobs/{job_id}/unprocessed-csv/preview` | No |
| `download_logs_zip` | `GET /api/runs/{run_id}/logs.zip` | No |
| `failure_summary` | `GET /api/jobs/{job_id}/error-csv/preview` (aggregated) | No |

`failure_summary` accepts a list of job UUIDs, fetches each job's error CSV,
groups rows by the `sf__Error` column, and returns the top-N failure reasons
ordered by frequency. It is the primary tool for explaining why records failed
after a run completes with errors.

`download_logs_zip` saves the ZIP to the OS-convention user data directory and
returns the saved path plus an archive member listing. ZIP bytes cannot be
inlined in an LLM response.

---

## Related

- [`mcp-server/docs/tool-binding.md`](../../mcp-server/docs/tool-binding.md) — binding contract, auth injection, error handling
- [`docs/architecture/mcp-server.md`](mcp-server.md) — system-level overview
- [`docs/usage/using-the-mcp-server.md`](../usage/using-the-mcp-server.md) — operator guide
