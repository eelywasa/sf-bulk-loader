"""Run-complete notification dispatch (SFBL-180 / SFBL-181).

Public surface:

- :class:`NotificationDispatcher` — selects matching subscriptions for a
  terminal run and fans each one out to the appropriate channel adapter.
- :func:`build_notification_dispatcher` — factory wiring the dispatcher to
  the configured :class:`EmailService` and the global ``AsyncSessionLocal``.
- :func:`init_notification_dispatcher` / :func:`get_notification_dispatcher`
  — module-level singleton management used by the FastAPI lifespan.
- :func:`fire_terminal_notifications` — fire-and-forget hook called by the
  orchestrator after a run reaches a terminal state.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.services.notifications.dispatcher import (
    NotificationDispatcher,
    build_notification_dispatcher,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from app.models.load_run import RunStatus
    from app.services.email.service import EmailService

logger = logging.getLogger(__name__)


_dispatcher: NotificationDispatcher | None = None

# In-memory guard to make ``fire_terminal_notifications`` idempotent per run.
# The orchestrator may reach a terminal state via multiple exit paths (e.g.
# step-loop cancellation + outer CancelledError backstop); without this guard
# a single run could produce duplicate notification_delivery rows.
_fired_run_ids: set[str] = set()
_FIRED_CACHE_CAP = 2048


def init_notification_dispatcher(
    email_service: "EmailService",
    session_factory: "async_sessionmaker[AsyncSession]",
) -> NotificationDispatcher:
    """Initialise the module-level dispatcher singleton.

    Called once from the FastAPI lifespan after ``init_email_service``.
    """
    global _dispatcher
    _dispatcher = build_notification_dispatcher(email_service, session_factory)
    return _dispatcher


def get_notification_dispatcher() -> NotificationDispatcher | None:
    """Return the singleton dispatcher, or ``None`` if not yet initialised."""
    return _dispatcher


def fire_terminal_notifications(run_id: str, run_status: "RunStatus") -> None:
    """Fire-and-forget dispatch for a terminal run.

    Spawns an ``asyncio.create_task`` so the orchestrator never blocks on
    notification I/O. Failures inside the task are logged but never
    propagated to the caller — notifications must not affect run status.

    No-op if the dispatcher has not been initialised (e.g. in tests that
    don't boot the app lifespan).
    """
    dispatcher = _dispatcher
    if dispatcher is None:
        return
    if run_id in _fired_run_ids:
        return
    if len(_fired_run_ids) >= _FIRED_CACHE_CAP:
        _fired_run_ids.clear()
    _fired_run_ids.add(run_id)

    async def _runner() -> None:
        try:
            await dispatcher.dispatch_run(run_id, run_status)
        except Exception:  # noqa: BLE001 — must never escape
            logger.exception(
                "Notification dispatch task crashed",
                extra={"run_id": run_id},
            )

    try:
        asyncio.create_task(_runner())
    except RuntimeError:
        # No running loop (unlikely — orchestrator always has one).
        logger.warning(
            "Notification dispatch skipped: no running event loop",
            extra={"run_id": run_id},
        )


__all__ = [
    "NotificationDispatcher",
    "build_notification_dispatcher",
    "init_notification_dispatcher",
    "get_notification_dispatcher",
    "fire_terminal_notifications",
]
