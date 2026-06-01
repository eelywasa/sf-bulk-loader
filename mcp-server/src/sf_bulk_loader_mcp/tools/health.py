"""Health check tool — hand-written, not OpenAPI-seeded.

The Salesforce Bulk Loader backend exposes ``GET /api/health/ready`` with
``include_in_schema=False``, so it never appears in ``/openapi.json``.  This
tool must be hand-written rather than generated from the OpenAPI spec.

See docs/tool-binding.md § "Hand-written tools" for the full rationale.
"""

from __future__ import annotations

from typing import Any

from ..client import BulkLoaderClient, McpHttpError


async def check_health(client: BulkLoaderClient) -> dict[str, Any]:
    """Call ``GET /api/health/ready`` and return the readiness payload.

    Returns:
        Dict with at least ``{"ready": bool, ...}`` fields from the backend.

    Raises:
        McpHttpError: The backend returned a non-2xx status.
        Exception:    Network error or backend unreachable.
    """
    response = await client.get("/api/health/ready")
    return response.json()


def format_health_result(payload: dict[str, Any]) -> str:
    """Format the health payload as a human-readable string for MCP callers."""
    ready = payload.get("ready", False)
    status_line = "Backend is ready." if ready else "Backend is NOT ready."
    details = {k: v for k, v in payload.items() if k != "ready"}
    if details:
        detail_str = ", ".join(f"{k}={v!r}" for k, v in details.items())
        return f"{status_line} ({detail_str})"
    return status_line
