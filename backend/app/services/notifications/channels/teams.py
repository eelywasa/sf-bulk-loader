"""TeamsWebhookChannel — POSTs an Adaptive Card to a Teams Workflows webhook.

MS Teams retired the classic Office 365 "Incoming Webhook" connectors (May
2026); the supported destination is a Power Automate **Workflows** webhook,
which renders an Adaptive Card wrapped in a ``message`` / ``attachments``
envelope and returns HTTP 202 on acceptance.  The Slack-compatible
``WebhookChannel`` payload is silently dropped by Teams, so this channel emits
a purpose-built card instead.  HTTP retry semantics are shared with
``WebhookChannel`` (see ``_shared.post_json_with_retry``).

Card design (validated against a live tenant on SFBL-352): a colored status
``Container`` banner, a ``FactSet`` of run metadata, and an ``Action.OpenUrl``
deep link to the run view.  Schema is pinned to v1.4 and uses only
``FactSet`` (v1.0) so it renders on Teams mobile (v1.2 ceiling).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from app.services.notifications.channels._shared import (
    ClientFactory,
    build_run_url,
    default_client_factory,
    post_json_with_retry,
)
from app.services.notifications.channels.base import ChannelResult

if TYPE_CHECKING:
    from app.models.notification_subscription import NotificationSubscription

_ADAPTIVE_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"
_SCHEMA_URL = "http://adaptivecards.io/schemas/adaptive-card.json"

# Run statuses that should render a red ("attention") banner; a clean
# completion renders green ("good"); anything else stays neutral.
_FAILED_STATES = {"failed", "aborted", "completed_with_errors"}


class TeamsWebhookChannel:
    name = "teams_webhook"

    def __init__(self, client_factory: "ClientFactory | None" = None) -> None:
        self._client_factory = client_factory or default_client_factory

    async def send(
        self,
        subscription: "NotificationSubscription",
        context: Mapping[str, Any],
    ) -> ChannelResult:
        payload = await _build_payload(context)
        return await post_json_with_retry(
            self._client_factory, subscription.destination, payload
        )


def _container_style(status: str) -> str:
    if status == "completed":
        return "good"
    if status in _FAILED_STATES:
        return "attention"
    return "default"


def _title(run: Mapping[str, Any], *, is_test: bool) -> str:
    plan = run.get("plan_name") or "Run"
    if is_test:
        return f"SFBL test notification: {plan}"
    status = run.get("status") or "finished"
    return f"Run {status}: {plan}"


def _facts(run: Mapping[str, Any]) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = [
        {"title": "Plan", "value": str(run.get("plan_name") or "—")},
        {"title": "Status", "value": str(run.get("status") or "—")},
    ]
    total = run.get("total_records")
    success = run.get("total_success")
    if total is not None or success is not None:
        facts.append(
            {"title": "Rows", "value": f"{(success or 0):,} / {(total or 0):,}"}
        )
    errors = run.get("total_errors")
    if errors is not None:
        facts.append({"title": "Failed", "value": f"{errors:,}"})
    if run.get("started_at"):
        facts.append({"title": "Started", "value": str(run["started_at"])})
    if run.get("completed_at"):
        facts.append({"title": "Finished", "value": str(run["completed_at"])})
    return facts


async def _build_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    run = context.get("run", {}) if isinstance(context, Mapping) else {}
    if not isinstance(run, Mapping):
        run = {}
    is_test = bool(context.get("is_test")) if isinstance(context, Mapping) else False
    status = str(run.get("status") or "")

    body: list[dict[str, Any]] = [
        {
            "type": "Container",
            "style": _container_style(status),
            "bleed": True,
            "items": [
                {
                    "type": "TextBlock",
                    "text": _title(run, is_test=is_test),
                    "size": "Large",
                    "weight": "Bolder",
                    "wrap": True,
                }
            ],
        },
        {"type": "FactSet", "facts": _facts(run)},
    ]

    card: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": _SCHEMA_URL,
        "version": "1.4",
        "body": body,
    }

    # Teams Action.OpenUrl requires an absolute URL. build_run_url returns a
    # root-relative path (e.g. /runs/<id>) when frontend_base_url is unset, which
    # Teams cannot navigate; omit the button rather than ship a dead deep link.
    run_url = await build_run_url(str(run.get("id") or ""))
    if run_url.startswith(("http://", "https://")):
        card["actions"] = [
            {
                "type": "Action.OpenUrl",
                "title": "View run in SFBL",
                "url": run_url,
            }
        ]

    return {
        "type": "message",
        "attachments": [
            {"contentType": _ADAPTIVE_CARD_CONTENT_TYPE, "content": card}
        ],
    }
