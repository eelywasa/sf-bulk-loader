"""Results and failure-inspection tools — job result CSV previews and logs download.

Implements SFBL-363: the final tool surface of the desktop MCP epic (SFBL-356).
After this story, no TODO tool entries remain in CURATED_TOOLS.

## Endpoint-to-tool mapping (all verified from backend routers)

  preview_success_csv    → GET /api/jobs/{job_id}/success-csv/preview
  preview_error_csv      → GET /api/jobs/{job_id}/error-csv/preview
  preview_unprocessed_csv→ GET /api/jobs/{job_id}/unprocessed-csv/preview
  download_logs_zip      → GET /api/runs/{run_id}/logs.zip
  failure_summary        → GET /api/jobs/{job_id}/error-csv/preview (aggregated across jobs)

All three preview endpoints accept the same query parameters:
  limit   (int, default 50, max 500)
  offset  (int, default 0)
  filters (JSON string — array of {column, value} objects)

The logs.zip endpoint returns binary zip bytes (an attachment).  Tools cannot
inline binary bytes for an LLM.  download_logs_zip streams the bytes to a
saved file and returns the path plus an archive member listing.

## Auth / permission notes

All preview and download endpoints are gated by ``files.view_contents``
(FILES_VIEW_CONTENTS in backend/app/auth/permissions.py).  In desktop profile
(auth_mode=none) the virtual admin user holds all permissions, so no auth
header is required.

## Row-limit and cell-byte caps

  DEFAULT_ROW_LIMIT   = 50    (matches backend default)
  MAX_ROW_LIMIT       = 500   (mirrors backend ``le=500`` Query constraint)
  MAX_CELL_BYTES      = 100_000  (100 KB — protects agent context window)

Both caps apply independently:
  - If the caller requests more than 500 rows, the request is clamped to 500.
  - Regardless of row count, if the total serialised cell content exceeds
    100 KB, rows are truncated and a "truncated" marker is appended.

The cell-byte cap is computed AFTER row-count clamping.  Truncation is
indicated by an extra entry in the ``rows`` list:
    {"__truncated__": "cell byte cap reached (100000 bytes max)"}

## logs.zip save location

The ZIP is saved to:
    <data_dir>/<app_name>/run-logs/run_<run_id_prefix>.zip

Where <data_dir> is the OS-convention user data directory resolved by
discovery.py._data_dir().  This mirrors where the Electron app stores
per-app state (~/Library/Application Support/Salesforce Bulk Loader/ on
macOS, etc.).

The path is configurable: the caller (server.py) passes the settings
object so the tool can read ``bulkloader_app_name`` (default:
"Salesforce Bulk Loader") and use the same OS-convention path as the
discovery file.

After saving, the tool also reads the archive with Python's ``zipfile``
module and returns the saved path + a listing of member names and sizes,
so the agent knows what's inside without unpacking it.

## failure_summary aggregation

failure_summary fetches up to MAX_ROW_LIMIT error rows for each job_id
in ``job_ids``, then groups them by the ``sf__Error`` column (the Salesforce
Bulk API error column in result CSVs).  It returns the top-N groups ordered
by frequency descending, plus a total error row count.

If a job's error CSV is not available (404) the job is silently skipped
with a note in the output.

## Redaction nuance

Tool RETURN VALUES legitimately contain CSV row data — that is their purpose.
Row data MUST NOT appear in structured log output (telemetry / observability
messages).  Callers must not log the return value of these tools to any
structured sink.  This is documented here and enforced in the test suite.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

from ..client import BulkLoaderClient, McpHttpError

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_ROW_LIMIT: int = 50
MAX_ROW_LIMIT: int = 500         # mirrors backend ``le=500`` Query constraint
MAX_CELL_BYTES: int = 100_000    # 100 KB — protects agent context window

# The Salesforce Bulk API error column in result CSVs.
_SF_ERROR_COLUMN = "sf__Error"

# Marker row appended when the cell-byte cap is reached.
_TRUNCATED_MARKER = {"__truncated__": f"cell byte cap reached ({MAX_CELL_BYTES} bytes max)"}

# Top-N groups returned by failure_summary.
_FAILURE_SUMMARY_TOP_N = 20

# Sub-directory within the OS data dir used for saved ZIP files.
_LOG_ZIP_SUBDIR = "run-logs"


# ── OS data-dir helper ─────────────────────────────────────────────────────────

def _resolve_data_dir(app_name: str = "sf-bulk-loader-desktop") -> Path:
    """Return the OS-convention user data directory for *app_name*.

    Matches discovery.py._data_dir() — kept local to avoid importing private
    symbols and to make the dependency explicit for tests.
    """
    import sys
    import os

    platform = sys.platform

    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name

    if platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / app_name
        return Path.home() / "AppData" / "Roaming" / app_name

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / app_name
    return Path.home() / ".config" / app_name


# ── Row-limit helpers ──────────────────────────────────────────────────────────

def _clamp_limit(limit: Optional[int]) -> int:
    """Return a row limit clamped to [1, MAX_ROW_LIMIT].

    None → DEFAULT_ROW_LIMIT.  Values above MAX_ROW_LIMIT are silently clamped.
    """
    if limit is None:
        return DEFAULT_ROW_LIMIT
    return max(1, min(int(limit), MAX_ROW_LIMIT))


def _apply_cell_byte_cap(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Truncate *rows* so the total serialised cell content stays under MAX_CELL_BYTES.

    Cells are counted as their JSON-serialised length (a reasonable proxy for
    in-memory footprint in the agent's context window).  If truncation happens,
    a sentinel ``_TRUNCATED_MARKER`` row is appended.

    NOTE: This operates on the result data (legitimate tool output) and must NOT
    log cell content — only counts.
    """
    total = 0
    result: list[dict[str, Any]] = []
    for row in rows:
        row_bytes = sum(len(json.dumps(v)) for v in row.values())
        if total + row_bytes > MAX_CELL_BYTES:
            result.append(_TRUNCATED_MARKER)
            logger.debug(
                "Cell byte cap reached",
                extra={
                    "event_name": "mcp.results.cell_byte_cap_reached",
                    "rows_included": len(result) - 1,
                    "rows_truncated": len(rows) - (len(result) - 1),
                },
            )
            break
        result.append(row)
        total += row_bytes
    return result


# ── Preview helpers ─────────────────────────────────────────────────────────────

def _build_preview_params(
    limit: int,
    offset: int,
    columns: Optional[list[str]],
    filters: Optional[list[dict[str, str]]],
) -> str:
    """Build the query string for a preview endpoint.

    ``columns`` is a client-side filter applied AFTER the backend returns rows;
    the backend itself has no column-projection parameter.  ``filters`` is
    serialised as a JSON string and passed as the ``filters`` query param.
    """
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if filters:
        # urlencode percent-escapes query-reserved chars (&, =, +, …) in the
        # JSON filter payload so arbitrary column values survive the round trip.
        params["filters"] = json.dumps(filters)
    return urlencode(params)


def _project_columns(
    payload: dict[str, Any], columns: Optional[list[str]]
) -> dict[str, Any]:
    """Return *payload* with row dicts narrowed to *columns* (if specified).

    The ``header``, ``rows``, ``offset``, ``limit``, ``has_next``,
    ``total_rows``, and ``filtered_rows`` keys are preserved; only the per-row
    dicts in ``rows`` are narrowed.
    """
    if not columns:
        return payload
    col_set = set(columns)
    projected_rows = [
        {k: v for k, v in row.items() if k in col_set}
        for row in (payload.get("rows") or [])
    ]
    # NOTE: projected row data must NOT be logged.
    return {**payload, "rows": projected_rows}


# ── CSV preview tools ──────────────────────────────────────────────────────────

async def preview_success_csv(
    client: BulkLoaderClient,
    job_id: str,
    *,
    limit: Optional[int] = None,
    offset: int = 0,
    columns: Optional[list[str]] = None,
    filters: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """Preview success result rows for a completed job.

    Calls ``GET /api/jobs/{job_id}/success-csv/preview`` (gated by
    files.view_contents; desktop no-auth passes automatically).

    Returns the backend preview payload (header, rows, pagination metadata),
    optionally narrowed to *columns*.  Row count is clamped to MAX_ROW_LIMIT;
    total cell bytes are capped to MAX_CELL_BYTES.
    """
    clamped = _clamp_limit(limit)
    qs = _build_preview_params(clamped, offset, columns, filters)
    response = await client.get(f"/api/jobs/{job_id}/success-csv/preview?{qs}")
    payload = response.json()
    payload = _project_columns(payload, columns)
    payload["rows"] = _apply_cell_byte_cap(payload.get("rows") or [])
    return payload


async def preview_error_csv(
    client: BulkLoaderClient,
    job_id: str,
    *,
    limit: Optional[int] = None,
    offset: int = 0,
    columns: Optional[list[str]] = None,
    filters: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """Preview error result rows for a completed job.

    Calls ``GET /api/jobs/{job_id}/error-csv/preview`` (gated by
    files.view_contents; desktop no-auth passes automatically).
    """
    clamped = _clamp_limit(limit)
    qs = _build_preview_params(clamped, offset, columns, filters)
    response = await client.get(f"/api/jobs/{job_id}/error-csv/preview?{qs}")
    payload = response.json()
    payload = _project_columns(payload, columns)
    payload["rows"] = _apply_cell_byte_cap(payload.get("rows") or [])
    return payload


async def preview_unprocessed_csv(
    client: BulkLoaderClient,
    job_id: str,
    *,
    limit: Optional[int] = None,
    offset: int = 0,
    columns: Optional[list[str]] = None,
    filters: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """Preview unprocessed result rows for a completed job.

    Calls ``GET /api/jobs/{job_id}/unprocessed-csv/preview`` (gated by
    files.view_contents; desktop no-auth passes automatically).
    """
    clamped = _clamp_limit(limit)
    qs = _build_preview_params(clamped, offset, columns, filters)
    response = await client.get(f"/api/jobs/{job_id}/unprocessed-csv/preview?{qs}")
    payload = response.json()
    payload = _project_columns(payload, columns)
    payload["rows"] = _apply_cell_byte_cap(payload.get("rows") or [])
    return payload


# ── logs.zip download ─────────────────────────────────────────────────────────


async def download_logs_zip(
    client: BulkLoaderClient,
    run_id: str,
    *,
    success: bool = True,
    errors: bool = True,
    unprocessed: bool = True,
    app_name: str = "sf-bulk-loader-desktop",
    data_dir_override: Optional[Path] = None,
) -> dict[str, Any]:
    """Download the logs.zip for a run, save it to disk, and return path + member listing.

    Calls ``GET /api/runs/{run_id}/logs.zip`` which returns binary ZIP bytes
    as an attachment.  Binary bytes cannot be inlined for an LLM, so this
    tool:

      1. Streams the response bytes.
      2. Saves the ZIP to ``<data_dir>/<_LOG_ZIP_SUBDIR>/run_<run_id[:8]>.zip``
         (OS-convention path matching the Electron userData dir).
      3. Opens the ZIP with Python's ``zipfile`` module and returns a member
         listing (name + size) alongside the saved file path.

    The saved path is stable and idempotent (same run_id → same path), so
    repeated calls overwrite the previous download.

    Args:
        client:             Authenticated BulkLoaderClient.
        run_id:             UUID of the load run.
        success:            Include success CSVs (default True).
        errors:             Include error CSVs (default True).
        unprocessed:        Include unprocessed CSVs (default True).
        app_name:           Electron productName — used to resolve the OS data dir.
        data_dir_override:  If set, saves to this directory instead of the
                            OS-convention path (used by tests).

    Returns:
        {
            "saved_path": "/abs/path/to/run_<prefix>.zip",
            "members": [{"name": "...", "size_bytes": ...}, ...],
            "member_count": <int>,
        }
    """
    # Build query string
    parts = []
    if not success:
        parts.append("success=false")
    if not errors:
        parts.append("errors=false")
    if not unprocessed:
        parts.append("unprocessed=false")

    url = f"/api/runs/{run_id}/logs.zip"
    if parts:
        url += "?" + "&".join(parts)

    # Stream the response — this call raises McpHttpError on non-2xx.
    response = await client.get(url)
    zip_bytes: bytes = response.content  # httpx Response.content is sync

    # Resolve save directory.
    if data_dir_override is not None:
        save_dir = data_dir_override / _LOG_ZIP_SUBDIR
    else:
        save_dir = _resolve_data_dir(app_name) / _LOG_ZIP_SUBDIR

    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / f"run_{run_id[:8]}.zip"

    save_path.write_bytes(zip_bytes)
    logger.info(
        "Saved logs zip",
        extra={
            "event_name": "mcp.results.logs_zip_saved",
            "run_id": run_id,
            "saved_path": str(save_path),
            "size_bytes": len(zip_bytes),
        },
    )

    # Read member listing — never inline bytes.
    members: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                members.append({"name": info.filename, "size_bytes": info.file_size})
    except zipfile.BadZipFile as exc:
        logger.warning(
            "Downloaded file is not a valid ZIP",
            extra={
                "event_name": "mcp.results.logs_zip_bad_format",
                "run_id": run_id,
                "error": type(exc).__name__,
            },
        )
        members = []

    return {
        "saved_path": str(save_path),
        "members": members,
        "member_count": len(members),
    }


# ── failure_summary ───────────────────────────────────────────────────────────


async def failure_summary(
    client: BulkLoaderClient,
    job_ids: list[str],
    *,
    top_n: int = _FAILURE_SUMMARY_TOP_N,
) -> dict[str, Any]:
    """Aggregate error reasons across one or more jobs and surface the top failures.

    For each job_id, fetches up to MAX_ROW_LIMIT error rows from
    ``GET /api/jobs/{job_id}/error-csv/preview`` and groups by the
    ``sf__Error`` column (the Salesforce Bulk API error column).

    Returns:
        {
            "total_error_rows": <int>,          # sum across all jobs
            "jobs_with_errors": <int>,           # jobs that returned at least 1 error row
            "jobs_unavailable": [<job_id>, ...], # jobs where error CSV was 404 / empty
            "top_errors": [                      # ordered by count desc, top_n max
                {"error": "<sf__Error value>", "count": <int>},
                ...
            ],
        }

    NOTE: row data (individual error record content) is NEVER logged by this
    function.  Only aggregate counts are emitted to telemetry.  The returned
    dict itself contains only aggregated data, not raw row content.
    """
    error_counts: Counter[str] = Counter()
    total_error_rows = 0
    jobs_with_errors = 0
    jobs_unavailable: list[str] = []

    for job_id in job_ids:
        qs = _build_preview_params(MAX_ROW_LIMIT, 0, None, None)
        try:
            response = await client.get(f"/api/jobs/{job_id}/error-csv/preview?{qs}")
        except McpHttpError as exc:
            # 404 → error CSV not available for this job; skip silently.
            if exc.status_code == 404:
                jobs_unavailable.append(job_id)
                continue
            # Other errors propagate.
            raise

        payload = response.json()
        rows: list[dict[str, Any]] = payload.get("rows") or []

        if not rows:
            jobs_unavailable.append(job_id)
            continue

        jobs_with_errors += 1
        for row in rows:
            total_error_rows += 1
            # sf__Error is the canonical Salesforce Bulk API error column.
            error_val = row.get(_SF_ERROR_COLUMN, "").strip()
            if error_val:
                error_counts[error_val] += 1
            else:
                error_counts["(no error message)"] += 1

    # Log aggregate only — never row content.
    logger.info(
        "Failure summary computed",
        extra={
            "event_name": "mcp.results.failure_summary_computed",
            "job_count": len(job_ids),
            "total_error_rows": total_error_rows,
            "jobs_with_errors": jobs_with_errors,
            "jobs_unavailable": len(jobs_unavailable),
            "unique_error_types": len(error_counts),
        },
    )

    top_errors = [
        {"error": err, "count": count}
        for err, count in error_counts.most_common(top_n)
    ]

    return {
        "total_error_rows": total_error_rows,
        "jobs_with_errors": jobs_with_errors,
        "jobs_unavailable": jobs_unavailable,
        "top_errors": top_errors,
    }


# ── Formatters ────────────────────────────────────────────────────────────────


def format_preview(
    payload: dict[str, Any],
    result_type: str,
    job_id: str,
) -> str:
    """Format a CSV preview payload as human-readable text.

    *result_type* is one of "success", "error", or "unprocessed".

    NOTE: The formatted output DOES include row data (that is the purpose of
    the preview tool).  This output must NOT be piped into structured logs.
    """
    rows = payload.get("rows") or []
    header = payload.get("header") or []
    offset = payload.get("offset", 0)
    limit = payload.get("limit", DEFAULT_ROW_LIMIT)
    has_next = payload.get("has_next", False)
    total_rows = payload.get("total_rows")
    filtered_rows = payload.get("filtered_rows")
    filename = payload.get("filename", "")

    lines: list[str] = [
        f"{result_type.capitalize()} CSV preview — job {job_id}",
        f"  file: {filename}",
        f"  columns: {', '.join(header) if header else '(none)'}",
    ]

    if total_rows is not None:
        lines.append(f"  total_rows_scanned: {total_rows}")
    if filtered_rows is not None:
        lines.append(f"  filtered_rows:      {filtered_rows}")

    lines.append(f"  offset: {offset}  limit: {limit}  has_next: {has_next}")

    # Check for truncation marker before counting
    real_rows = [r for r in rows if "__truncated__" not in r]
    truncated = len(real_rows) < len(rows)

    lines.append(f"  rows returned: {len(real_rows)}" + (" (cell byte cap reached)" if truncated else ""))
    lines.append("")

    if header and real_rows:
        # Render as a simple fixed-width table
        col_widths = {col: max(len(col), max(len(str(row.get(col, ""))) for row in real_rows)) for col in header}
        header_row = "  " + "  ".join(col.ljust(col_widths[col]) for col in header)
        sep = "  " + "  ".join("-" * col_widths[col] for col in header)
        lines.append(header_row)
        lines.append(sep)
        for row in real_rows:
            lines.append("  " + "  ".join(str(row.get(col, "")).ljust(col_widths[col]) for col in header))
        if truncated:
            lines.append("  ... (truncated — cell byte cap reached)")
    elif not real_rows:
        lines.append("  (no rows)")

    return "\n".join(lines)


def format_download_logs_zip(payload: dict[str, Any], run_id: str) -> str:
    """Format the result of download_logs_zip as human-readable text."""
    saved_path = payload.get("saved_path", "")
    members = payload.get("members") or []
    member_count = payload.get("member_count", 0)

    lines = [
        f"Logs ZIP downloaded for run {run_id}",
        f"  saved to: {saved_path}",
        f"  members ({member_count}):",
    ]
    for m in members:
        size_kb = m["size_bytes"] / 1024
        lines.append(f"    {m['name']}  ({size_kb:.1f} KB)")
    if not members:
        lines.append("    (archive is empty or could not be read)")

    return "\n".join(lines)


def format_failure_summary(payload: dict[str, Any]) -> str:
    """Format a failure_summary result as human-readable text."""
    total = payload.get("total_error_rows", 0)
    jobs_with = payload.get("jobs_with_errors", 0)
    unavailable = payload.get("jobs_unavailable") or []
    top_errors = payload.get("top_errors") or []

    lines = [
        "Failure summary",
        f"  total error rows:   {total}",
        f"  jobs with errors:   {jobs_with}",
    ]
    if unavailable:
        lines.append(f"  jobs unavailable:   {len(unavailable)} ({', '.join(unavailable)})")

    if top_errors:
        lines.append(f"\n  Top error reasons (by frequency):")
        for i, entry in enumerate(top_errors, 1):
            lines.append(f"    {i:2}. [{entry['count']:>5}x]  {entry['error']}")
    else:
        lines.append("\n  No error rows found.")

    return "\n".join(lines)
