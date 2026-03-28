"""Starlette middleware for recording HTTP request metrics.

Records per-request latency and totals into the Prometheus-compatible
metrics defined in :mod:`app.observability.metrics`. Excludes the
``/metrics`` endpoint itself to avoid self-referential noise.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.observability.metrics import http_request_duration_seconds, http_requests_total

_EXCLUDE_PATHS = frozenset({"/metrics", "/metrics/"})


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record HTTP request count and latency for every non-metrics request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _EXCLUDE_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        status_code = 500  # default; overwritten on success
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            raise
        finally:
            duration = time.perf_counter() - start
            method = request.method
            status_class = f"{status_code // 100}xx"
            http_requests_total.labels(method=method, status_class=status_class).inc()
            http_request_duration_seconds.labels(method=method).observe(duration)
