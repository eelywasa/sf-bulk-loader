"""Execution and monitoring tools — load runs and job records.

Implements SFBL-362: wrap the backend run lifecycle (trigger, abort, retry-step),
run listing/inspection, and job listing/inspection routes as MCP tools.

## Endpoint-to-tool mapping (all verified from backend routers)

  trigger_run   → POST /api/load-plans/{plan_id}/run       (load_plans.py router, prefix=/api/load-plans)
  list_runs     → GET  /api/runs/                          (load_runs.py router, prefix=/api/runs)
  get_run       → GET  /api/runs/{run_id}                  (load_runs.py router, prefix=/api/runs)
  abort_run     → POST /api/runs/{run_id}/abort            (load_runs.py router, prefix=/api/runs)
  retry_step    → POST /api/runs/{run_id}/retry-step/{step_id} (load_runs.py router)
  list_jobs     → GET  /api/runs/{run_id}/jobs             (jobs.py router, no prefix — full path in route)
  get_job       → GET  /api/jobs/{job_id}                  (jobs.py router, no prefix — full path in route)

## Destructive-action safety

trigger_run, abort_run, and retry_step execute real Bulk DML or abort live
Salesforce Bulk API jobs.  They all carry:
  - ToolAnnotations(destructiveHint=True) on their Tool definitions.
  - A required ``confirm: true`` boolean in their inputSchema.  The handler
    makes NO backend call if ``confirm`` is absent or False, and returns a
    structured refusal message telling the caller to pass confirm=true.

## Monitoring approach — REST polling, NOT WebSocket

All tools provide single-shot status reads so an agent can poll at its own
cadence.  The WebSocket at ``/ws/runs/{run_id}`` is intentionally NOT used
here: the ``validate_ws_token`` path bypasses ``get_current_user`` (it uses a
dedicated short-lived token) and is out of scope for the MCP channel.

### Recommended poll cadence for agents waiting on a terminal run status:

  1. Call ``trigger_run`` to start a run; note the returned ``run_id``.
  2. Poll ``get_run(run_id)`` every 5–10 seconds.
  3. A terminal status is one of: ``completed``, ``failed``, ``aborted``.
  4. Apply exponential backoff: start at 5 s, double on each non-terminal
     response, cap at 60 s.  Enforce a hard timeout of ~30 minutes for
     production loads (adjust for expected data volume).
  5. On each poll, inspect ``job_records`` in the response for per-partition
     status and error details.

## Observability note

MCP adds NO new run/job instrumentation here.  All lifecycle events —
RunEvent.STARTED, RunEvent.COMPLETED, JobEvent.CREATED, etc. — are already
emitted by the backend orchestrator via ``app/observability/events.py``.
Duplicating them in the MCP layer would cause double-counting in any
metrics pipeline.  See ``docs/observability.md`` for the full event catalogue.
"""

from __future__ import annotations

from typing import Any, Optional

from ..client import BulkLoaderClient

# ── Constants ──────────────────────────────────────────────────────────────────

_PLANS_PREFIX = "/api/load-plans"
_RUNS_PREFIX = "/api/runs"

# Refusal message returned when a destructive tool is called without confirm=true.
_CONFIRM_REQUIRED = (
    "This tool performs a destructive action (real Bulk API DML or live-run mutation). "
    "You must pass confirm=true to proceed.  "
    "Re-call this tool with the same arguments plus `confirm=true` to execute."
)


# ── Trigger run ───────────────────────────────────────────────────────────────

async def trigger_run(client: BulkLoaderClient, plan_id: str) -> dict[str, Any]:
    """Call ``POST /api/load-plans/{plan_id}/run`` and return the new LoadRun payload.

    NOTE: This endpoint lives on the **load-plans** router (prefix=/api/load-plans),
    NOT on the load-runs router.  It was verified from backend/app/api/load_plans.py
    at the ``start_load_run`` route handler.
    """
    response = await client.post(f"{_PLANS_PREFIX}/{plan_id}/run")
    return response.json()


# ── List / get runs ──────────────────────────────────────────────────────────

async def list_runs(
    client: BulkLoaderClient,
    *,
    plan_id: Optional[str] = None,
    run_status: Optional[str] = None,
    started_after: Optional[str] = None,
    started_before: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Call ``GET /api/runs/`` with optional filters and return the run list.

    Filter parameters map to the backend query params:
      - plan_id      (str UUID)
      - run_status   (RunStatus enum string: pending/running/completed/failed/aborted)
      - started_after / started_before (ISO-8601 datetime strings)
    """
    params: dict[str, str] = {}
    if plan_id is not None:
        params["plan_id"] = plan_id
    if run_status is not None:
        params["run_status"] = run_status
    if started_after is not None:
        params["started_after"] = started_after
    if started_before is not None:
        params["started_before"] = started_before

    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{_RUNS_PREFIX}/?{qs}"
    else:
        url = f"{_RUNS_PREFIX}/"
    response = await client.get(url)
    return response.json()


async def get_run(client: BulkLoaderClient, run_id: str) -> dict[str, Any]:
    """Call ``GET /api/runs/{run_id}`` and return the LoadRunDetailResponse (with jobs)."""
    response = await client.get(f"{_RUNS_PREFIX}/{run_id}")
    return response.json()


# ── Abort run ─────────────────────────────────────────────────────────────────

async def abort_run(client: BulkLoaderClient, run_id: str) -> dict[str, Any]:
    """Call ``POST /api/runs/{run_id}/abort`` and return the updated LoadRun payload."""
    response = await client.post(f"{_RUNS_PREFIX}/{run_id}/abort")
    return response.json()


# ── Retry step ────────────────────────────────────────────────────────────────

async def retry_step(
    client: BulkLoaderClient, run_id: str, step_id: str
) -> dict[str, Any]:
    """Call ``POST /api/runs/{run_id}/retry-step/{step_id}`` and return the new LoadRun.

    Creates a new LoadRun that retries only the failed/aborted jobs of the
    given step in the original run.  Returns the new run (not the original).
    """
    response = await client.post(f"{_RUNS_PREFIX}/{run_id}/retry-step/{step_id}")
    return response.json()


# ── List / get jobs ──────────────────────────────────────────────────────────

async def list_jobs(
    client: BulkLoaderClient,
    run_id: str,
    *,
    step_id: Optional[str] = None,
    job_status: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Call ``GET /api/runs/{run_id}/jobs`` and return the job list.

    Optional filters:
      - step_id   (str UUID) — only jobs for a specific step
      - job_status (JobStatus string: pending/running/completed/failed/aborted)

    NOTE: The jobs router has no prefix; the full path is embedded in the
    route decorator as ``/api/runs/{run_id}/jobs`` (verified from
    backend/app/api/jobs.py).
    """
    params: dict[str, str] = {}
    if step_id is not None:
        params["step_id"] = step_id
    if job_status is not None:
        params["job_status"] = job_status

    base = f"/api/runs/{run_id}/jobs"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{base}?{qs}"
    else:
        url = base
    response = await client.get(url)
    return response.json()


async def get_job(client: BulkLoaderClient, job_id: str) -> dict[str, Any]:
    """Call ``GET /api/jobs/{job_id}`` and return the JobResponse payload.

    NOTE: The jobs router has no prefix; the full path is ``/api/jobs/{job_id}``
    (verified from backend/app/api/jobs.py).
    """
    response = await client.get(f"/api/jobs/{job_id}")
    return response.json()


# ── Formatters ────────────────────────────────────────────────────────────────


def format_run(payload: dict[str, Any], *, include_jobs: bool = False) -> str:
    """Format a LoadRunResponse or LoadRunDetailResponse as human-readable text."""
    error_summary = payload.get("error_summary") or {}
    error_parts: list[str] = []
    if isinstance(error_summary, dict):
        for key in ("auth_error", "storage_error", "circuit_breaker"):
            val = error_summary.get(key)
            if val:
                error_parts.append(f"    {key}: {val}")
        preflight = error_summary.get("preflight_warnings") or []
        if preflight:
            error_parts.append(f"    preflight_warnings: {len(preflight)} warning(s)")

    lines = [
        f"Load run: {payload['id']}",
        f"  status:          {payload['status']}",
        f"  load_plan_id:    {payload['load_plan_id']}",
        f"  initiated_by:    {payload.get('initiated_by') or '(unknown)'}",
        f"  started_at:      {payload.get('started_at') or '—'}",
        f"  completed_at:    {payload.get('completed_at') or '—'}",
        f"  total_records:   {payload.get('total_records') if payload.get('total_records') is not None else '—'}",
        f"  total_success:   {payload.get('total_success') if payload.get('total_success') is not None else '—'}",
        f"  total_errors:    {payload.get('total_errors') if payload.get('total_errors') is not None else '—'}",
        f"  is_retry:        {payload.get('is_retry', False)}",
    ]
    if payload.get("retry_of_run_id"):
        lines.append(f"  retry_of_run_id: {payload['retry_of_run_id']}")
    if error_parts:
        lines.append("  error_summary:")
        lines.extend(error_parts)

    if include_jobs:
        jobs = payload.get("jobs") or []
        lines.append(f"  jobs ({len(jobs)}):")
        for j in jobs:
            err_note = f" | error: {j['error_message']}" if j.get("error_message") else ""
            processed = j.get("records_processed")
            failed = j.get("records_failed")
            counts = ""
            if processed is not None:
                counts = f" | processed={processed}"
                if failed is not None:
                    counts += f" failed={failed}"
            lines.append(
                f"    [{j.get('partition_index', '?')}] id={j['id']}"
                f"  step={j['load_step_id']}"
                f"  status={j['status']}"
                f"{counts}{err_note}"
            )

    return "\n".join(lines)


def format_trigger_run(payload: dict[str, Any]) -> str:
    return f"Load run triggered successfully.\n{format_run(payload)}"


def format_abort_run(payload: dict[str, Any]) -> str:
    return f"Load run abort requested.\n{format_run(payload)}"


def format_retry_step(payload: dict[str, Any]) -> str:
    return f"Step retry run created.\n{format_run(payload)}"


def format_list_runs(payload: list[dict[str, Any]]) -> str:
    if not payload:
        return "No load runs found."
    lines = [f"Load runs ({len(payload)}):"]
    for r in payload:
        ts = r.get("started_at") or "—"
        lines.append(
            f"  id={r['id']}  plan={r['load_plan_id']}"
            f"  status={r['status']}"
            f"  started_at={ts}"
        )
    return "\n".join(lines)


def format_job(payload: dict[str, Any]) -> str:
    """Format a single JobResponse as human-readable text."""
    err_note = f"\n  error_message:    {payload['error_message']}" if payload.get("error_message") else ""
    sf_link = ""
    if payload.get("sf_instance_url") and payload.get("sf_job_id"):
        sf_link = f"\n  sf_job_url:       {payload['sf_instance_url']}/services/async/{payload['sf_job_id']}"
    lines = [
        f"Job: {payload['id']}",
        f"  load_run_id:      {payload['load_run_id']}",
        f"  load_step_id:     {payload['load_step_id']}",
        f"  partition_index:  {payload.get('partition_index', '?')}",
        f"  status:           {payload['status']}",
        f"  total_records:    {payload.get('total_records') if payload.get('total_records') is not None else '—'}",
        f"  records_processed:{payload.get('records_processed') if payload.get('records_processed') is not None else '—'}",
        f"  records_failed:   {payload.get('records_failed') if payload.get('records_failed') is not None else '—'}",
        f"  records_successful:{payload.get('records_successful') if payload.get('records_successful') is not None else '—'}",
        f"  started_at:       {payload.get('started_at') or '—'}",
        f"  completed_at:     {payload.get('completed_at') or '—'}",
        f"  sf_job_id:        {payload.get('sf_job_id') or '—'}",
    ]
    extra = sf_link + err_note
    if extra:
        lines.append(extra.lstrip("\n"))
    return "\n".join(lines)


def format_list_jobs(payload: list[dict[str, Any]]) -> str:
    if not payload:
        return "No jobs found."
    lines = [f"Jobs ({len(payload)}):"]
    for j in payload:
        err_note = f" | error: {j['error_message']}" if j.get("error_message") else ""
        processed = j.get("records_processed")
        failed = j.get("records_failed")
        counts = ""
        if processed is not None:
            counts = f"  processed={processed}"
            if failed is not None:
                counts += f" failed={failed}"
        lines.append(
            f"  [{j.get('partition_index', '?')}] id={j['id']}"
            f"  step={j['load_step_id']}"
            f"  status={j['status']}"
            f"{counts}{err_note}"
        )
    return "\n".join(lines)


# ── Destructive confirm gate ──────────────────────────────────────────────────

def check_confirm(arguments: dict[str, Any]) -> str | None:
    """Return a refusal string if ``confirm`` is absent/False; else None.

    Destructive tools (trigger_run, abort_run, retry_step) call this before
    making any backend request.  If it returns a non-None string, the handler
    must return that string immediately WITHOUT calling the backend.
    """
    if arguments.get("confirm") is True:
        return None
    return _CONFIRM_REQUIRED
