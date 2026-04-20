"""Run-complete notification dispatch (SFBL-180).

Public surface:

- :class:`NotificationDispatcher` — selects matching subscriptions for a
  terminal run and fans each one out to the appropriate channel adapter.
- :func:`build_notification_dispatcher` — factory wiring the dispatcher to
  the configured :class:`EmailService` and the global ``AsyncSessionLocal``.
"""

from app.services.notifications.dispatcher import (
    NotificationDispatcher,
    build_notification_dispatcher,
)

__all__ = ["NotificationDispatcher", "build_notification_dispatcher"]
