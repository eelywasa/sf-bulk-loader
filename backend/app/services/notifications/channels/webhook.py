"""WebhookChannel — POSTs the run-complete payload to the subscription URL.

Slack-compatible envelope (``text`` + ``run``).  The HTTP retry loop is shared
with ``TeamsWebhookChannel`` via ``_shared.post_json_with_retry`` (retries only
on 5xx / 429 / network errors; 4xx terminal; ``attempt_count`` 1..3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from app.services.notifications.channels._shared import (
    ClientFactory,
    default_client_factory,
    post_json_with_retry,
)
from app.services.notifications.channels.base import ChannelResult

if TYPE_CHECKING:
    from app.models.notification_subscription import NotificationSubscription


class WebhookChannel:
    name = "webhook"

    def __init__(self, client_factory: "ClientFactory | None" = None) -> None:
        # Tests inject a client_factory to replace the real httpx client
        # with a respx-backed transport.
        self._client_factory = client_factory or default_client_factory

    async def send(
        self,
        subscription: "NotificationSubscription",
        context: Mapping[str, Any],
    ) -> ChannelResult:
        payload = _build_payload(context)
        return await post_json_with_retry(
            self._client_factory, subscription.destination, payload
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
