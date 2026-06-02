"""Async HTTP client for the Salesforce Bulk Loader backend.

This module owns:
  - Base URL + auth header injection (from config/discovery; NEVER from
    OpenAPI servers/security fields).
  - Central HTTP→MCP error mapping: 4xx/5xx → structured McpHttpError.
  - Safe error formatting: no raw stack traces are ever surfaced to MCP callers.
  - Structured auth-failure logging (SFBL-371): 401 in PAT mode emits a
    WARNING log with event_name="MCP_PAT_AUTH_FAILURE". The PAT value is
    NEVER present in any log record (sanitization rule).

Auth mode behaviour:
  none (desktop):  No Authorization header.  Base URL resolved lazily from
                   discovery.py on first request (so the server can start even
                   before the Electron app has written the discovery file).
  pat  (hosted):   Authorization: Bearer <token>.  Base URL from config.
                   Static token per session; no rotation in v1.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, AsyncGenerator, Optional

import httpx

from .config import AuthMode, McpSettings
from .discovery import resolve_base_url

logger = logging.getLogger(__name__)


# ── Public error type ─────────────────────────────────────────────────────────

class McpHttpError(Exception):
    """Structured HTTP error safe to surface as a MCP tool error.

    Attributes:
        status_code: HTTP status code from the backend.
        message:     Human-readable summary (never a raw stack trace).
        detail:      Backend error detail if the response was JSON, else None.
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        detail: Optional[Any] = None,
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.detail = detail
        super().__init__(message)

    def to_tool_error_text(self) -> str:
        """Return a safe single-string representation for MCP tool error content."""
        parts = [f"HTTP {self.status_code}: {self.message}"]
        if self.detail:
            parts.append(f"Detail: {self.detail}")
        return " | ".join(parts)


# ── HTTP → MCP error mapping ──────────────────────────────────────────────────

def _map_http_error(response: httpx.Response) -> McpHttpError:
    """Convert a non-2xx httpx response to a structured McpHttpError.

    Extracts the backend ``detail`` field when the response is JSON (FastAPI
    convention).  Falls back to a safe summary for non-JSON bodies.  Raw
    response bodies are NEVER passed through verbatim.
    """
    detail: Optional[Any] = None
    message: str

    try:
        body = response.json()
        detail = body.get("detail") if isinstance(body, dict) else None
    except Exception:
        body = None

    status = response.status_code

    if status == 400:
        message = "Bad request"
    elif status == 401:
        message = "Unauthorized — check your PAT or auth configuration"
    elif status == 403:
        message = "Forbidden — insufficient permissions"
    elif status == 404:
        message = "Resource not found"
    elif status == 409:
        message = "Conflict — resource already exists or state mismatch"
    elif status == 422:
        message = "Unprocessable request — validation error"
    elif status == 429:
        message = "Rate limited — too many requests"
    elif 500 <= status < 600:
        message = f"Backend server error (HTTP {status})"
    else:
        message = f"Unexpected HTTP {status}"

    return McpHttpError(status_code=status, message=message, detail=detail)


# ── Client ────────────────────────────────────────────────────────────────────

class BulkLoaderClient:
    """Thin async HTTP wrapper around the Salesforce Bulk Loader REST API.

    Instantiate once and reuse (keeps a persistent httpx.AsyncClient).
    Call ``aclose()`` when done, or use it as an async context manager.

    The base URL is resolved lazily on the first request in desktop/none mode
    so the MCP server can start before the Electron app writes the discovery
    file.  In pat mode the URL is available at construction time.
    """

    def __init__(self, settings: McpSettings) -> None:
        self._settings = settings
        self._resolved_base_url: Optional[str] = None
        self._http: Optional[httpx.AsyncClient] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> "BulkLoaderClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    # ── Internal helpers ───────────────────────────────────────────────────

    def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    def _resolve_base_url(self) -> str:
        """Resolve the backend base URL (once, then cached)."""
        if self._resolved_base_url is not None:
            return self._resolved_base_url

        url = resolve_base_url(
            explicit_url=self._settings.bulkloader_base_url,
            app_name=self._settings.bulkloader_app_name,
        )
        self._resolved_base_url = url
        return url

    def _build_headers(self) -> dict[str, str]:
        """Build request headers, injecting auth if needed."""
        headers: dict[str, str] = {
            "Accept": "application/json",
        }
        if self._settings.auth_mode == AuthMode.PAT and self._settings.bulkloader_pat:
            # config.py validates that bulkloader_pat is set when auth_mode=PAT,
            # so this branch is always taken in a well-configured PAT session.
            # The PAT value is injected here and NOWHERE ELSE — never logged.
            headers["Authorization"] = f"Bearer {self._settings.bulkloader_pat}"
        return headers

    def _log_auth_failure_if_401(self, error: McpHttpError, path: str) -> None:
        """Emit a structured WARNING when a 401 occurs in PAT mode (SFBL-371).

        Sanitization rule: the PAT value is NEVER included in the log record.
        The event is structured so it can be correlated by ops tooling.
        """
        if error.status_code == 401 and self._settings.auth_mode == AuthMode.PAT:
            logger.warning(
                "MCP PAT authentication failed — verify BULKLOADER_PAT is valid "
                "and not expired. Mint a new token in Settings → API Tokens.",
                extra={
                    "event_name": "MCP_PAT_AUTH_FAILURE",
                    "auth_mode": "pat",
                    "status_code": 401,
                    "path": path,
                    # PAT value intentionally omitted — sanitization rule
                },
            )

    # ── Public request helpers ─────────────────────────────────────────────

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a GET request, raising McpHttpError on non-2xx responses."""
        base = self._resolve_base_url()
        url = f"{base}{path}"
        response = await self._get_http().get(url, headers=self._build_headers(), **kwargs)
        if not response.is_success:
            error = _map_http_error(response)
            self._log_auth_failure_if_401(error, path)
            raise error
        return response

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a POST request, raising McpHttpError on non-2xx responses."""
        base = self._resolve_base_url()
        url = f"{base}{path}"
        response = await self._get_http().post(url, headers=self._build_headers(), **kwargs)
        if not response.is_success:
            error = _map_http_error(response)
            self._log_auth_failure_if_401(error, path)
            raise error
        return response

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a PUT request, raising McpHttpError on non-2xx responses."""
        base = self._resolve_base_url()
        url = f"{base}{path}"
        response = await self._get_http().put(url, headers=self._build_headers(), **kwargs)
        if not response.is_success:
            error = _map_http_error(response)
            self._log_auth_failure_if_401(error, path)
            raise error
        return response

    async def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a DELETE request, raising McpHttpError on non-2xx responses."""
        base = self._resolve_base_url()
        url = f"{base}{path}"
        response = await self._get_http().delete(url, headers=self._build_headers(), **kwargs)
        if not response.is_success:
            error = _map_http_error(response)
            self._log_auth_failure_if_401(error, path)
            raise error
        return response
