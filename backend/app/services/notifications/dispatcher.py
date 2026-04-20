"""NotificationDispatcher — selects matching subscriptions and fans out.

Per D2 (SFBL-117):
    terminal_any         → fires on completed | completed_with_errors | failed | aborted
    terminal_fail_only   → fires on completed_with_errors | failed | aborted

Per D3: exactly one ``notification_delivery`` row per dispatch. Email retries
stay in ``email_delivery``; webhook retries are internal to the channel.

Public entry points:

- ``dispatch_run(run_id, run_status)`` — fan-out for a terminal run.  Used
  by the orchestrator hook (SFBL-181).
- ``dispatch_one(subscription, run=None, is_test=False)`` — single-subscription
  dispatch used by the ``/test`` endpoint (SFBL-182) and internally by
  ``dispatch_run``.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.load_run import LoadRun, RunStatus
from app.models.notification_delivery import (
    NotificationDelivery,
    NotificationDeliveryStatus,
)
from app.models.notification_subscription import (
    NotificationChannel as SubscriptionChannel,
    NotificationSubscription,
    NotificationTrigger,
)
from app.observability.events import NotificationEvent, OutcomeCode
from app.observability.metrics import (
    notification_dispatch_duration_seconds,
    notification_dispatch_total,
)
from app.observability.sanitization import (
    redact_email_address,
    sanitize_webhook_url,
)
from app.services.notifications.channels.base import (
    ChannelResult,
    NotificationChannel,
)
from app.services.notifications.channels.email import EmailChannel
from app.services.notifications.channels.webhook import WebhookChannel

if TYPE_CHECKING:
    from app.services.email.service import EmailService

logger = logging.getLogger(__name__)


_TERMINAL_ANY_STATES = {
    RunStatus.completed,
    RunStatus.completed_with_errors,
    RunStatus.failed,
    RunStatus.aborted,
}
_TERMINAL_FAIL_STATES = {
    RunStatus.completed_with_errors,
    RunStatus.failed,
    RunStatus.aborted,
}


def _triggers_for_status(status: RunStatus) -> set[NotificationTrigger]:
    """Which trigger values should fire for *status* (per D2)."""
    matches: set[NotificationTrigger] = set()
    if status in _TERMINAL_ANY_STATES:
        matches.add(NotificationTrigger.terminal_any)
    if status in _TERMINAL_FAIL_STATES:
        matches.add(NotificationTrigger.terminal_fail_only)
    return matches


class NotificationDispatcher:
    """Dispatch terminal-run notifications to subscribed channels."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        channels: Mapping[SubscriptionChannel, NotificationChannel],
    ) -> None:
        self._session_factory = session_factory
        self._channels = dict(channels)

    async def dispatch_run(
        self, run_id: str, run_status: RunStatus
    ) -> list[NotificationDelivery]:
        """Fan out to every subscription matching (plan_id, run_status).

        Returns the list of ``notification_delivery`` rows created (one per
        matching subscription), or an empty list if none matched.
        """
        triggers = _triggers_for_status(run_status)
        if not triggers:
            return []

        async with self._session_factory() as session:
            from sqlalchemy.orm import selectinload

            run = await session.get(
                LoadRun, run_id, options=[selectinload(LoadRun.load_plan)]
            )
            if run is None:
                logger.warning(
                    "Notification dispatch skipped: run not found",
                    extra={
                        "event_name": NotificationEvent.DISPATCH_FAILED,
                        "outcome_code": OutcomeCode.VALIDATION_ERROR,
                        "run_id": run_id,
                    },
                )
                return []
            plan_id = run.load_plan_id

            stmt = select(NotificationSubscription).where(
                NotificationSubscription.trigger.in_(triggers),
                or_(
                    NotificationSubscription.plan_id == plan_id,
                    NotificationSubscription.plan_id.is_(None),
                ),
            )
            result = await session.execute(stmt)
            subs = list(result.scalars().all())

            # Detach LoadRun from the session so we can close it before
            # calling out to channels (which may be slow / do their own I/O).
            run_snapshot = _snapshot_run(run)

        if not subs:
            logger.info(
                "No notification subscriptions match terminal run",
                extra={
                    "event_name": NotificationEvent.NO_MATCHING_SUBSCRIPTIONS,
                    "outcome_code": OutcomeCode.OK,
                    "run_id": run_id,
                    "run_status": run_status.value,
                    "plan_id": plan_id,
                },
            )
            return []

        context = _build_context(run_snapshot)
        deliveries: list[NotificationDelivery] = []
        for sub in subs:
            deliveries.append(await self._dispatch(sub, run_id, context, is_test=False))
        return deliveries

    async def dispatch_one(
        self,
        subscription: NotificationSubscription,
        run: LoadRun | None = None,
        *,
        is_test: bool = False,
        context: Mapping[str, Any] | None = None,
    ) -> NotificationDelivery:
        """Dispatch to a single subscription (used by /test and fan-out)."""
        if context is None:
            run_ctx = _snapshot_run(run) if run is not None else {"status": "test"}
            context = _build_context(run_ctx, is_test=is_test)
        run_id = run.id if run is not None else None
        return await self._dispatch(subscription, run_id, context, is_test=is_test)

    async def _dispatch(
        self,
        subscription: NotificationSubscription,
        run_id: str | None,
        context: Mapping[str, Any],
        *,
        is_test: bool,
    ) -> NotificationDelivery:
        channel_name = subscription.channel
        destination_safe = _safe_destination(subscription)
        logger.info(
            "Notification dispatch requested",
            extra={
                "event_name": NotificationEvent.DISPATCH_REQUESTED,
                "outcome_code": OutcomeCode.OK,
                "subscription_id": subscription.id,
                "channel": channel_name.value,
                "destination": destination_safe,
                "run_id": run_id,
                "is_test": is_test,
            },
        )

        async with self._session_factory() as session:
            delivery = NotificationDelivery(
                subscription_id=subscription.id,
                run_id=run_id,
                is_test=is_test,
                status=NotificationDeliveryStatus.pending,
                attempt_count=0,
            )
            session.add(delivery)
            await session.flush()  # assign delivery.id

            channel = self._channels.get(channel_name)
            if channel is None:
                delivery.status = NotificationDeliveryStatus.failed
                delivery.attempt_count = 0
                delivery.last_error = f"No channel registered for {channel_name.value!r}"
                await session.commit()
                await session.refresh(delivery)
                notification_dispatch_total.labels(
                    channel=channel_name.value, status="failed"
                ).inc()
                logger.error(
                    "Notification dispatch failed: no channel",
                    extra={
                        "event_name": NotificationEvent.DISPATCH_FAILED,
                        "outcome_code": OutcomeCode.CONFIGURATION_ERROR,
                        "subscription_id": subscription.id,
                        "channel": channel_name.value,
                    },
                )
                return delivery

            t_start = time.monotonic()
            try:
                result: ChannelResult = await channel.send(subscription, context)
            except Exception as exc:  # noqa: BLE001 — belt-and-braces
                from app.observability.sanitization import safe_exc_message

                result = ChannelResult(
                    accepted=False,
                    attempts=1,
                    error_detail=safe_exc_message(exc),
                )

            elapsed = time.monotonic() - t_start
            notification_dispatch_duration_seconds.labels(
                channel=channel_name.value
            ).observe(elapsed)

            delivery.attempt_count = result.attempts
            delivery.email_delivery_id = result.email_delivery_id
            if result.accepted:
                delivery.status = NotificationDeliveryStatus.sent
                delivery.sent_at = datetime.now(tz=timezone.utc)
                delivery.last_error = None
                notification_dispatch_total.labels(
                    channel=channel_name.value, status="sent"
                ).inc()
                logger.info(
                    "Notification dispatch succeeded",
                    extra={
                        "event_name": NotificationEvent.DISPATCH_SUCCEEDED,
                        "outcome_code": OutcomeCode.OK,
                        "subscription_id": subscription.id,
                        "channel": channel_name.value,
                        "delivery_id": delivery.id,
                        "attempts": result.attempts,
                    },
                )
            else:
                delivery.status = NotificationDeliveryStatus.failed
                delivery.last_error = result.error_detail
                outcome = (
                    OutcomeCode.NOTIFICATION_WEBHOOK_ERROR
                    if channel_name == SubscriptionChannel.webhook
                    else OutcomeCode.FAILED
                )
                notification_dispatch_total.labels(
                    channel=channel_name.value, status="failed"
                ).inc()
                logger.warning(
                    "Notification dispatch failed",
                    extra={
                        "event_name": NotificationEvent.DISPATCH_FAILED,
                        "outcome_code": outcome,
                        "subscription_id": subscription.id,
                        "channel": channel_name.value,
                        "delivery_id": delivery.id,
                        "attempts": result.attempts,
                        "error": result.error_detail,
                    },
                )

            await session.commit()
            await session.refresh(delivery)
            return delivery


def _safe_destination(subscription: NotificationSubscription) -> str:
    if subscription.channel == SubscriptionChannel.email:
        return redact_email_address(subscription.destination)
    return sanitize_webhook_url(subscription.destination)


def _snapshot_run(run: LoadRun | Mapping[str, Any]) -> dict[str, Any]:
    """Freeze the LoadRun attributes we want in the notification context.

    Accepts either an ORM object or a plain mapping (used by /test).  Only
    primitive-typed fields are copied so the resulting dict is safe to
    serialise across task boundaries without carrying a detached session.
    """
    if isinstance(run, Mapping):
        return dict(run)
    plan_name = None
    if getattr(run, "load_plan", None) is not None:
        plan_name = run.load_plan.name
    return {
        "id": run.id,
        "load_plan_id": run.load_plan_id,
        "plan_name": plan_name,
        "status": run.status.value if hasattr(run.status, "value") else run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "total_records": run.total_records,
        "total_success": run.total_success,
        "total_errors": run.total_errors,
    }


def _build_context(
    run: Mapping[str, Any], *, is_test: bool = False
) -> dict[str, Any]:
    """Template/payload context shared by all channels.

    SFBL-181 will expand this with plan name + step/job counts via a
    plan-aware loader.  For SFBL-180 the channel contract only requires
    ``run`` + ``is_test`` + ``text``; additional keys are ignored by both
    channels.
    """
    return {
        "run": dict(run),
        "is_test": is_test,
        "text": _summary_text(run, is_test=is_test),
    }


def _summary_text(run: Mapping[str, Any], *, is_test: bool) -> str:
    if is_test:
        return "SFBL notification test"
    status = run.get("status", "finished")
    return f"Load run {run.get('id', '')} {status}"


# ── Factory ─────────────────────────────────────────────────────────────────


def build_notification_dispatcher(
    email_service: "EmailService",
    session_factory: async_sessionmaker[AsyncSession],
) -> NotificationDispatcher:
    """Assemble the default dispatcher with email + webhook channels wired up."""
    return NotificationDispatcher(
        session_factory=session_factory,
        channels={
            SubscriptionChannel.email: EmailChannel(email_service),
            SubscriptionChannel.webhook: WebhookChannel(),
        },
    )
