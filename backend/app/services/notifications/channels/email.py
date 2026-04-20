"""EmailChannel — delegates to the existing EmailService.

Per D3 on SFBL-117, this channel does NOT own retries.  `EmailService`
records attempts on its own ``email_delivery`` row; the returned row's id
is stored on the ``notification_delivery`` row as a pointer so operators
can cross-reference the two logs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Mapping

from app.config import settings
from app.observability.sanitization import safe_exc_message
from app.services.email.message import EmailCategory
from app.services.notifications.channels.base import ChannelResult

if TYPE_CHECKING:
    from app.models.notification_subscription import NotificationSubscription
    from app.services.email.service import EmailService

logger = logging.getLogger(__name__)

_TEMPLATE_NAME = "notifications/run_complete"


class EmailChannel:
    name = "email"

    def __init__(self, email_service: "EmailService") -> None:
        self._email = email_service

    async def send(
        self,
        subscription: "NotificationSubscription",
        context: Mapping[str, Any],
    ) -> ChannelResult:
        template_context = _flatten_context(context)
        try:
            delivery = await self._email.send_template(
                _TEMPLATE_NAME,
                template_context,
                to=subscription.destination,
                category=EmailCategory.NOTIFICATION,
            )
        except Exception as exc:  # noqa: BLE001 — sanitise and record as failure
            logger.warning(
                "Notification email send raised",
                extra={"error": safe_exc_message(exc)},
            )
            return ChannelResult(
                accepted=False,
                attempts=1,
                error_detail=safe_exc_message(exc),
            )

        # ``status`` is a str column on EmailDelivery; enum values are
        # defined in app.services.email.delivery_log.DeliveryStatus.  A
        # ``pending`` row means EmailService hit a transient failure and
        # scheduled a retry — the send is still in flight, so we must not
        # record it as a hard failure on the notification row.
        status = getattr(delivery, "status", None)
        accepted = status in {"sent", "skipped"}
        pending = (not accepted) and status == "pending"
        return ChannelResult(
            accepted=accepted,
            attempts=1,
            error_detail=None if accepted else getattr(delivery, "last_error_msg", None),
            email_delivery_id=delivery.id,
            pending=pending,
        )


def _flatten_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Produce the flat key set required by ``notifications/run_complete``.

    The dispatcher passes a shared context of shape ``{"run": {...}, "is_test":
    bool, "text": str}`` — useful for webhook consumers but a mismatch for the
    template manifest's strict flat contract.  This helper projects those nested
    fields out to the exact keys the template enforces (``plan_name``,
    ``run_id``, ``status``, ``total_rows``, ``success_rows``, ``failed_rows``,
    ``started_at``, ``ended_at``, ``run_url``).  Missing numeric totals default
    to 0 rather than None so the template can render without conditional
    guards.
    """
    run = context.get("run") if isinstance(context, Mapping) else None
    run = dict(run) if isinstance(run, Mapping) else {}
    run_id = run.get("id", "")
    return {
        "plan_name": run.get("plan_name") or "Untitled plan",
        "run_id": run_id,
        "status": run.get("status", "unknown"),
        "total_rows": run.get("total_records") or 0,
        "success_rows": run.get("total_success") or 0,
        "failed_rows": run.get("total_errors") or 0,
        "started_at": run.get("started_at") or "",
        "ended_at": run.get("completed_at") or "",
        "run_url": _build_run_url(run_id),
    }


def _build_run_url(run_id: str) -> str:
    base = (settings.frontend_base_url or "").rstrip("/")
    if not run_id:
        return base or ""
    if not base:
        return f"/runs/{run_id}"
    return f"{base}/runs/{run_id}"
