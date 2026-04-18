"""In-memory sliding-window rate limiter and FastAPI dependency factory.

Design notes
------------
The backing store is a plain ``dict[str, deque[float]]`` protected by a
module-level ``asyncio.Lock``.  This keeps the implementation simple and
dependency-free, but has two implications:

1. **Per-process only** — limits are not shared across multiple worker
   processes or containers.  If you run the backend behind a multi-process
   ASGI server (e.g. ``gunicorn -w 4``) each worker maintains its own
   counter, so the effective limit is ``limit × workers``.  For the
   single-worker Docker deployment this codebase ships with that is not an
   issue.

2. **Redis upgrade path** — the public API (``check_and_record`` + the
   ``rate_limit`` dependency factory) is intentionally narrow so that a
   Redis or Valkey backend can be swapped in later without changing any call
   sites.  The helper signatures and semantics are stable.

Usage example
~~~~~~~~~~~~~
::

    from app.services.rate_limit import rate_limit, ip_key

    router = APIRouter()

    _ip_limiter = rate_limit(ip_key, limit=5, window_seconds=3600)

    @router.post("/forgot-password", dependencies=[Depends(_ip_limiter)])
    async def forgot_password(...):
        ...
"""

import asyncio
import hashlib
import time
from collections import deque
from typing import Callable

from fastapi import Depends, HTTPException, Request, status

# ── Internal store ────────────────────────────────────────────────────────────

_store: dict[str, deque[float]] = {}
_lock: asyncio.Lock = asyncio.Lock()


# ── Core primitive ────────────────────────────────────────────────────────────


async def check_and_record(key: str, limit: int, window_seconds: int) -> bool:
    """Check whether *key* is within its rate limit and, if so, record the hit.

    This is the single entry-point for all rate-limit logic.  It uses a
    sliding-window algorithm: only timestamps within the last *window_seconds*
    seconds count toward *limit*.

    Args:
        key:            An arbitrary string that identifies the bucket
                        (e.g. ``"pw_reset:ip:1.2.3.4"`` or
                        ``"pw_reset:email:<sha256>"``).
        limit:          Maximum number of hits allowed inside *window_seconds*.
        window_seconds: Width of the sliding window in seconds.

    Returns:
        ``True``  — the request is within the limit (hit has been recorded).
        ``False`` — the limit has been exceeded (hit has **not** been recorded).
    """
    now = time.monotonic()
    cutoff = now - window_seconds

    async with _lock:
        timestamps = _store.setdefault(key, deque())

        # Reap stale entries (reap-on-touch)
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

        if len(timestamps) >= limit:
            return False

        timestamps.append(now)
        return True


# ── FastAPI dependency factory ────────────────────────────────────────────────


def rate_limit(
    key_builder: Callable[[Request], str],
    *,
    limit: int,
    window_seconds: int,
) -> Callable:
    """Return a FastAPI dependency that enforces a sliding-window rate limit.

    Args:
        key_builder:    A callable that accepts a :class:`fastapi.Request` and
                        returns the bucket key string.  Typical builders are
                        :func:`ip_key` and :func:`hashed_email_key`.
        limit:          Maximum hits allowed per *window_seconds*.
        window_seconds: Sliding-window width in seconds.

    Returns:
        A coroutine suitable for use with ``Depends()``.

    Raises:
        :class:`fastapi.HTTPException` (429) when the limit is exceeded.

    Example::

        _limiter = rate_limit(ip_key, limit=5, window_seconds=3600)

        @router.post("/forgot-password", dependencies=[Depends(_limiter)])
        async def forgot_password(...):
            ...
    """

    async def _dependency(request: Request) -> None:
        key = key_builder(request)
        allowed = await check_and_record(key, limit, window_seconds)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests",
            )

    return _dependency


# ── Key-builder helpers ───────────────────────────────────────────────────────


def ip_key(request: Request) -> str:
    """Return a rate-limit key scoped to the client IP address.

    Reads ``request.client.host`` (set by Starlette from the ASGI scope).
    Falls back to ``"unknown"`` if the host is not available (e.g. tests
    without a real transport layer).

    Example key: ``"rl:ip:1.2.3.4"``
    """
    host = (request.client.host if request.client else None) or "unknown"
    return f"rl:ip:{host}"


def hashed_email_key(email: str) -> str:
    """Return a rate-limit key derived from an email address.

    The address is SHA-256 hashed so the raw address is never stored in
    the in-memory bucket map.  Use this when keying limits to a submitted
    email field (e.g. the password-reset request body).

    Example key: ``"rl:email:<sha256hex>"``
    """
    digest = hashlib.sha256(email.lower().encode()).hexdigest()
    return f"rl:email:{digest}"
