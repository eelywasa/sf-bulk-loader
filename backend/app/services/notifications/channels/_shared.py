"""Shared helpers for HTTP-POST notification channels (webhook, teams_webhook).

Both the Slack-compatible ``WebhookChannel`` and the ``TeamsWebhookChannel``
POST a JSON body to a user-supplied HTTPS endpoint with identical retry
semantics, and both want a deep link back into the SFBL run view.  The retry
loop and the run-URL builder live here so the two channels stay in lock-step
rather than drifting.

Retry policy (per D3 on SFBL-117): retry only on 5xx, 429, or network errors;
any 2xx (including the 202 the Teams Workflows trigger returns) is success;
other 4xx is terminal.  ``attempts`` reflects the number of HTTP attempts
actually made (1..3).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Callable

import httpx

from app.observability.events import NotificationEvent, OutcomeCode
from app.observability.metrics import notification_webhook_retry_total
from app.observability.sanitization import (
    safe_exc_message,
    sanitize_webhook_url,
)
from app.services.notifications.channels.base import ChannelResult

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
_BASE_BACKOFF_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 10.0

ClientFactory = Callable[[], httpx.AsyncClient]


def default_client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)


def _backoff(attempt_idx: int) -> float:
    raw = _BASE_BACKOFF_SECONDS * (2**attempt_idx)
    return raw + random.uniform(0, _BASE_BACKOFF_SECONDS)


def _log_retry(safe_url: str, attempt: int, error: str, reason: str) -> None:
    logger.warning(
        "Notification webhook retry scheduled",
        extra={
            "event_name": NotificationEvent.WEBHOOK_RETRIED,
            "outcome_code": OutcomeCode.OK,
            "webhook_url": safe_url,
            "attempt": attempt,
            "reason": reason,
            "error": error,
        },
    )


async def post_json_with_retry(
    client_factory: ClientFactory,
    destination: str,
    payload: dict,
) -> ChannelResult:
    """POST *payload* as JSON to *destination*, retrying transient failures.

    Shared by ``WebhookChannel`` and ``TeamsWebhookChannel``.  Any 2xx is
    accepted (Teams Workflows returns 202); 5xx/429/network errors are retried
    up to ``MAX_ATTEMPTS``; other 4xx is terminal.
    """
    safe_url = sanitize_webhook_url(destination)
    last_error: str | None = None
    attempts = 0

    async with client_factory() as client:
        for idx in range(MAX_ATTEMPTS):
            attempts = idx + 1
            try:
                response = await client.post(destination, json=payload)
            except httpx.HTTPError as exc:
                last_error = safe_exc_message(exc)
                if attempts >= MAX_ATTEMPTS:
                    break
                notification_webhook_retry_total.labels(reason="network").inc()
                _log_retry(safe_url, attempts, last_error, "network")
                await asyncio.sleep(_backoff(idx))
                continue

            status = response.status_code
            if 200 <= status < 300:
                return ChannelResult(accepted=True, attempts=attempts)

            # Retryable server / throttle responses
            if status >= 500 or status == 429:
                last_error = f"HTTP {status}"
                reason = "throttled" if status == 429 else "server_error"
                if attempts >= MAX_ATTEMPTS:
                    break
                notification_webhook_retry_total.labels(reason=reason).inc()
                _log_retry(safe_url, attempts, last_error, reason)
                await asyncio.sleep(_backoff(idx))
                continue

            # Terminal 4xx
            return ChannelResult(
                accepted=False,
                attempts=attempts,
                error_detail=f"HTTP {status}",
            )

    return ChannelResult(
        accepted=False,
        attempts=attempts,
        error_detail=last_error,
    )


async def get_frontend_base_url() -> str:
    """Resolve ``frontend_base_url`` from SettingsService (empty if unset)."""
    try:
        from app.services.settings.service import settings_service as _svc
        if _svc is not None:
            return (await _svc.get("frontend_base_url")) or ""
    except Exception:
        pass
    return ""


async def build_run_url(run_id: str) -> str:
    """Build an absolute (or root-relative) URL to the run detail view."""
    base = (await get_frontend_base_url()).rstrip("/")
    if not run_id:
        return base or ""
    if not base:
        return f"/runs/{run_id}"
    return f"{base}/runs/{run_id}"
