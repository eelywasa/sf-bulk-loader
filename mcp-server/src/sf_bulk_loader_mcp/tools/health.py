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

    The backend conveys readiness via a ``status`` field (``"ok"`` when ready),
    optionally with per-dependency status objects — NOT a ``ready`` boolean.

    Returns:
        Dict shaped like ``{"status": "ok", "dependencies": {...}}``.

    Raises:
        McpHttpError: The backend returned a non-2xx status.
        Exception:    Network error or backend unreachable.
    """
    response = await client.get("/api/health/ready")
    return response.json()


def format_health_result(payload: dict[str, Any]) -> str:
    """Format the health payload as a human-readable string for MCP callers.

    Readiness is conveyed by the backend's ``status`` field (``"ok"`` => ready);
    there is no ``ready`` boolean in the response.
    """
    ready = payload.get("status") == "ok"
    status_line = "Backend is ready." if ready else "Backend is NOT ready."
    detail_str = ", ".join(f"{k}={v!r}" for k, v in payload.items())
    return f"{status_line} ({detail_str})" if detail_str else status_line
