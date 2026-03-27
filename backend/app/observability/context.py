"""Shared observability context variables.

This module owns the ContextVar instances used to propagate per-request
correlation identifiers across the async call stack. It has no framework
dependencies so it can be imported safely by both middleware and logging
modules without risk of circular imports.

Usage:
    from app.observability.context import get_request_id, request_id_ctx_var

    # In middleware — set for the duration of the request:
    token = request_id_ctx_var.set(request_id)
    try:
        ...
    finally:
        request_id_ctx_var.reset(token)

    # Anywhere in the call stack — read the current value:
    rid = get_request_id()  # returns None outside a request context
"""

from __future__ import annotations

from contextvars import ContextVar

request_id_ctx_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Return the request ID for the current async context, or None."""
    return request_id_ctx_var.get()
