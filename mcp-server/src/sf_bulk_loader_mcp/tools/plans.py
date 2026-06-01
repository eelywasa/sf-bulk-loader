"""Plan and step authoring tools — load plans, load steps, and validation helpers.

Implements SFBL-361: wrap the backend load-plan CRUD, load-step CRUD/reorder,
SOQL validation, and step-preview routes as MCP tools.

Input schemas match the backend request/response shapes documented in
``backend/app/schemas/load_plan.py`` and ``backend/app/schemas/load_step.py``.

The real API prefix is ``/api/load-plans`` (NOT ``/api/plans`` as guessed in the
SFBL-359 CURATED_TOOLS placeholder).  All paths in this module use the correct
prefix verified from the router file.

Key acceptance-criteria features:
  - ``output_connection_id`` is exposed on plan create/update (backend validates
    its direction is 'out' or 'both').
  - ``input_from_step_id`` is exposed on step create/update (query-step output
    chaining as per SFBL-166).
  - Backend validation errors (422) are passed through clearly via the
    McpHttpError → structured-text mapping.

See docs/tool-binding.md for the full contract.
"""

from __future__ import annotations

from typing import Any

from ..client import BulkLoaderClient

# ── Constants ──────────────────────────────────────────────────────────────────

_PLANS_PREFIX = "/api/load-plans"


# ── Helper: build endpoint path ───────────────────────────────────────────────

def _plan_url(plan_id: str) -> str:
    return f"{_PLANS_PREFIX}/{plan_id}"


def _steps_url(plan_id: str) -> str:
    return f"{_PLANS_PREFIX}/{plan_id}/steps"


def _step_url(plan_id: str, step_id: str) -> str:
    return f"{_PLANS_PREFIX}/{plan_id}/steps/{step_id}"


# ── Plans — call helpers ──────────────────────────────────────────────────────


async def list_plans(client: BulkLoaderClient) -> list[dict[str, Any]]:
    """Call ``GET /api/load-plans/`` and return the list payload."""
    response = await client.get(f"{_PLANS_PREFIX}/")
    return response.json()


def format_list_plans(payload: list[dict[str, Any]]) -> str:
    if not payload:
        return "No load plans found."
    lines = [f"Load plans ({len(payload)}):"]
    for p in payload:
        step_count = len(p.get("load_steps", []))
        steps_note = f"  [{step_count} step(s)]" if step_count else ""
        out_conn = f"  output_connection={p['output_connection_id']!r}" if p.get("output_connection_id") else ""
        lines.append(
            f"  id={p['id']}  name={p['name']!r}  connection={p['connection_id']!r}"
            f"{out_conn}{steps_note}"
        )
    return "\n".join(lines)


async def get_plan(client: BulkLoaderClient, plan_id: str) -> dict[str, Any]:
    """Call ``GET /api/load-plans/{plan_id}`` and return the plan with nested steps."""
    response = await client.get(_plan_url(plan_id))
    return response.json()


def format_plan(payload: dict[str, Any]) -> str:
    steps = payload.get("load_steps", [])
    lines = [
        f"Load plan: {payload['name']!r}",
        f"  id:                           {payload['id']}",
        f"  connection_id:                {payload['connection_id']}",
        f"  output_connection_id:         {payload.get('output_connection_id') or '(none)'}",
        f"  abort_on_step_failure:        {payload.get('abort_on_step_failure', True)}",
        f"  error_threshold_pct:          {payload.get('error_threshold_pct', 10.0)}",
        f"  max_parallel_jobs:            {payload.get('max_parallel_jobs', 5)}",
        f"  consecutive_failure_threshold:{payload.get('consecutive_failure_threshold', 5)}",
        f"  description:                  {payload.get('description') or '(none)'}",
        f"  created_at:                   {payload.get('created_at', 'N/A')}",
        f"  updated_at:                   {payload.get('updated_at', 'N/A')}",
        f"  steps ({len(steps)}):",
    ]
    for s in steps:
        input_from = f" ← step {s['input_from_step_id']}" if s.get("input_from_step_id") else ""
        pattern = f" pattern={s['csv_file_pattern']!r}" if s.get("csv_file_pattern") else ""
        soql_note = " [SOQL]" if s.get("soql") else ""
        lines.append(
            f"    [{s.get('sequence', '?')}] id={s['id']}  op={s['operation']} "
            f"object={s['object_name']!r}{pattern}{soql_note}{input_from}"
        )
    return "\n".join(lines)


async def create_plan(client: BulkLoaderClient, body: dict[str, Any]) -> dict[str, Any]:
    """Call ``POST /api/load-plans/`` and return the created plan payload."""
    response = await client.post(f"{_PLANS_PREFIX}/", json=body)
    return response.json()


def format_create_plan(payload: dict[str, Any]) -> str:
    return f"Load plan created successfully.\n{format_plan(payload)}"


async def update_plan(
    client: BulkLoaderClient, plan_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Call ``PUT /api/load-plans/{plan_id}`` and return the updated plan payload."""
    response = await client.put(_plan_url(plan_id), json=body)
    return response.json()


def format_update_plan(payload: dict[str, Any]) -> str:
    return f"Load plan updated successfully.\n{format_plan(payload)}"


async def delete_plan(client: BulkLoaderClient, plan_id: str) -> None:
    """Call ``DELETE /api/load-plans/{plan_id}`` (204 No Content)."""
    await client.delete(_plan_url(plan_id))


def format_delete_plan(plan_id: str) -> str:
    return f"Load plan {plan_id!r} deleted successfully."


async def duplicate_plan(client: BulkLoaderClient, plan_id: str) -> dict[str, Any]:
    """Call ``POST /api/load-plans/{plan_id}/duplicate`` and return the new plan payload."""
    response = await client.post(f"{_plan_url(plan_id)}/duplicate")
    return response.json()


def format_duplicate_plan(payload: dict[str, Any]) -> str:
    return f"Load plan duplicated successfully.\n{format_plan(payload)}"


# ── Steps — call helpers ──────────────────────────────────────────────────────


async def add_step(
    client: BulkLoaderClient, plan_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Call ``POST /api/load-plans/{plan_id}/steps`` and return the created step payload."""
    response = await client.post(_steps_url(plan_id), json=body)
    return response.json()


def format_step(payload: dict[str, Any]) -> str:
    input_from = f"\n  input_from_step_id: {payload['input_from_step_id']}" if payload.get("input_from_step_id") else ""
    pattern = f"\n  csv_file_pattern:  {payload['csv_file_pattern']!r}" if payload.get("csv_file_pattern") else ""
    soql = f"\n  soql:              {payload['soql']!r}" if payload.get("soql") else ""
    input_conn = f"\n  input_connection_id: {payload['input_connection_id']}" if payload.get("input_connection_id") else ""
    name_line = f"\n  name:              {payload['name']!r}" if payload.get("name") else ""
    lines = [
        f"Load step: sequence={payload.get('sequence', '?')}  op={payload['operation']}  object={payload['object_name']!r}",
        f"  id:                {payload['id']}",
        f"  load_plan_id:      {payload['load_plan_id']}",
        f"  external_id_field: {payload.get('external_id_field') or '(none)'}",
        f"  partition_size:    {payload.get('partition_size') or '(default)'}",
        f"  assignment_rule_id:{payload.get('assignment_rule_id') or '(none)'}",
        f"  created_at:        {payload.get('created_at', 'N/A')}",
        f"  updated_at:        {payload.get('updated_at', 'N/A')}",
    ]
    # Append optional fields only when present
    extra = name_line + pattern + soql + input_conn + input_from
    if extra:
        lines.append(extra.lstrip("\n"))
    return "\n".join(lines)


def format_add_step(payload: dict[str, Any]) -> str:
    return f"Step added successfully.\n{format_step(payload)}"


async def update_step(
    client: BulkLoaderClient, plan_id: str, step_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Call ``PUT /api/load-plans/{plan_id}/steps/{step_id}`` and return the updated step payload."""
    response = await client.put(_step_url(plan_id, step_id), json=body)
    return response.json()


def format_update_step(payload: dict[str, Any]) -> str:
    return f"Step updated successfully.\n{format_step(payload)}"


async def delete_step(client: BulkLoaderClient, plan_id: str, step_id: str) -> None:
    """Call ``DELETE /api/load-plans/{plan_id}/steps/{step_id}`` (204 No Content)."""
    await client.delete(_step_url(plan_id, step_id))


def format_delete_step(step_id: str) -> str:
    return f"Step {step_id!r} deleted successfully."


async def reorder_steps(
    client: BulkLoaderClient, plan_id: str, step_ids: list[str]
) -> list[dict[str, Any]]:
    """Call ``POST /api/load-plans/{plan_id}/steps/reorder`` and return the reordered steps."""
    response = await client.post(
        f"{_steps_url(plan_id)}/reorder",
        json={"step_ids": step_ids},
    )
    return response.json()


def format_reorder_steps(payload: list[dict[str, Any]]) -> str:
    if not payload:
        return "No steps returned after reorder."
    lines = [f"Steps reordered ({len(payload)} steps):"]
    for s in payload:
        lines.append(
            f"  seq={s.get('sequence', '?')}  id={s['id']}  op={s['operation']}  object={s['object_name']!r}"
        )
    return "\n".join(lines)


# ── Validation helpers — call helpers ─────────────────────────────────────────


async def validate_soql(
    client: BulkLoaderClient, plan_id: str, soql: str
) -> dict[str, Any]:
    """Call ``POST /api/load-plans/{plan_id}/validate-soql`` and return the validation result."""
    response = await client.post(
        f"{_plan_url(plan_id)}/validate-soql",
        json={"soql": soql},
    )
    return response.json()


def format_validate_soql(payload: dict[str, Any]) -> str:
    valid = payload.get("valid", False)
    status_word = "VALID" if valid else "INVALID"
    lines = [f"SOQL validation: {status_word}"]
    if payload.get("error"):
        lines.append(f"  error: {payload['error']}")
    if payload.get("plan"):
        lines.append(f"  explain plan: {payload['plan']}")
    return "\n".join(lines)


async def preview_step(
    client: BulkLoaderClient, plan_id: str, step_id: str
) -> dict[str, Any]:
    """Call ``POST /api/load-plans/{plan_id}/steps/{step_id}/preview`` and return the preview result."""
    response = await client.post(f"{_step_url(plan_id, step_id)}/preview")
    return response.json()


def format_preview_step(payload: dict[str, Any]) -> str:
    kind = payload.get("kind", "dml")
    lines = [f"Step preview (kind={kind!r}):"]
    if kind == "query":
        valid = payload.get("valid")
        if valid is True:
            lines.append("  SOQL: VALID")
            if payload.get("plan"):
                lines.append(f"  explain plan: {payload['plan']}")
        elif valid is False:
            lines.append(f"  SOQL: INVALID — {payload.get('error', 'unknown error')}")
        else:
            lines.append("  SOQL: not validated")
    else:
        # DML step
        if payload.get("note"):
            lines.append(f"  note: {payload['note']}")
        elif payload.get("pattern"):
            matched = payload.get("matched_files", [])
            total = payload.get("total_rows", 0)
            lines.append(f"  pattern:      {payload['pattern']!r}")
            lines.append(f"  matched files:{len(matched)}")
            lines.append(f"  total rows:   {total}")
            for f in matched:
                lines.append(f"    {f['filename']} — {f['row_count']} rows")
        else:
            lines.append("  No file pattern configured.")
    return "\n".join(lines)
