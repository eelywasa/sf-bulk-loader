"""WebhookChannel — POSTs the run-complete payload to the subscription URL.

Owns its own retry loop (per D3).  Retries only on 5xx responses, 429, or
network errors.  4xx is terminal.  ``attempt_count`` reflects how many
HTTP attempts were actually made (1..3 by default).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING, Any, Mapping

import httpx

from app.observability.events import NotificationEvent, OutcomeCode
from app.observability.metrics import notification_webhook_retry_total
from app.observability.sanitization import (
    safe_exc_message,
    sanitize_webhook_url,
)
from app.services.notifications.channels.base import ChannelResult

if TYPE_CHECKING:
    from app.models.notification_subscription import NotificationSubscription

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BASE_BACKOFF_SECONDS = 1.0
_REQUEST_TIMEOUT_SECONDS = 10.0


def _backoff(attempt_idx: int) -> float:
    raw = _BASE_BACKOFF_SECONDS * (2**attempt_idx)
    return raw + random.uniform(0, _BASE_BACKOFF_SECONDS)


class WebhookChannel:
    name = "webhook"

    def __init__(
        self,
        client_factory: "callable[[], httpx.AsyncClient] | None" = None,
    ) -> None:
        # Tests inject a client_factory to replace the real httpx client
        # with a respx-backed transport.
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS)
        )

    async def send(
        self,
        subscription: "NotificationSubscription",
        context: Mapping[str, Any],
    ) -> ChannelResult:
        destination = subscription.destination
        safe_url = sanitize_webhook_url(destination)
        payload = _build_payload(context)
        last_error: str | None = None
        attempts = 0

        async with self._client_factory() as client:
            for idx in range(_MAX_ATTEMPTS):
                attempts = idx + 1
                try:
                    response = await client.post(destination, json=payload)
                except httpx.HTTPError as exc:
                    last_error = safe_exc_message(exc)
                    notification_webhook_retry_total.labels(reason="network").inc()
                    if attempts >= _MAX_ATTEMPTS:
                        break
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
                    notification_webhook_retry_total.labels(reason=reason).inc()
                    if attempts >= _MAX_ATTEMPTS:
                        break
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


def _build_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    """Slack-compatible envelope per the SFBL-117 spec sample.

    Keeps the ``text`` top-level field for Slack's simple incoming-webhook
    contract, and nests the structured run metadata under ``run`` so generic
    HTTP endpoints can parse the fuller shape.
    """
    run = context.get("run", {}) if isinstance(context, Mapping) else {}
    text = context.get("text") or _default_text(run)
    return {"text": text, "run": dict(run)}


def _default_text(run: Mapping[str, Any]) -> str:
    plan = run.get("plan_name") or run.get("load_plan_id") or "Run"
    status = run.get("status", "finished")
    return f"{plan}: {status}"


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
