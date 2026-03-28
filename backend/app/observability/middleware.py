"""Request ID middleware for the Salesforce Bulk Loader backend.

Stamps every inbound HTTP request with a stable correlation ID, binds it
into the async context so all log calls within the request automatically
include it, echoes it in the response header, and emits a structured
access log entry on completion.

Request ID resolution order:
  1. Trusted upstream header (settings.request_id_header_name) — adopted as-is.
  2. Not present — generated as uuid4().hex (32-char lowercase hex string).

The generated/adopted ID is:
  - Set in request_id_ctx_var for the duration of the request.
  - Added to the response under the same header name.
  - Included in the structured access log record via extra={}.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.observability.context import request_id_ctx_var

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a request ID to every HTTP request and emit an access log entry."""

    def __init__(self, app, settings: "Settings") -> None:
        super().__init__(app)
        self._header_name = settings.request_id_header_name

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(self._header_name) or uuid.uuid4().hex

        token = request_id_ctx_var.set(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            response.headers[self._header_name] = request_id
            logger.info(
                "request completed",
                extra={
                    "event_name": "http.request",
                    "request_id": request_id,
                    "method": request.method,
                    "route": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            return response
        except Exception:
            # In FastAPI, route-level exceptions are converted to responses by
            # ExceptionMiddleware before reaching this middleware, so this path
            # fires only for infra-level failures. Build a 500 response so that
            # the request ID header is still included in the error reply —
            # correlation is most valuable when things go wrong.
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            logger.exception(
                "request failed with unhandled exception",
                extra={
                    "event_name": "http.request",
                    "request_id": request_id,
                    "method": request.method,
                    "route": request.url.path,
                    "duration_ms": duration_ms,
                },
            )
            return Response(
                status_code=500,
                headers={self._header_name: request_id},
            )
        finally:
            request_id_ctx_var.reset(token)
