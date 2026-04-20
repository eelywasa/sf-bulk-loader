"""Channel protocol for notification dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol

if TYPE_CHECKING:
    from app.models.notification_subscription import NotificationSubscription


@dataclass(slots=True)
class ChannelResult:
    """Outcome of a single channel dispatch call.

    One ChannelResult maps to one ``notification_delivery`` row update. For
    email channels, ``attempts`` is always 1 — the underlying EmailService
    owns retries and records them on its own ``email_delivery`` row.  For
    webhook channels, ``attempts`` is the number of HTTP attempts this
    channel made (1..retry_budget).
    """

    accepted: bool
    attempts: int = 1
    error_detail: str | None = None
    email_delivery_id: str | None = None
    # True when the underlying transport has neither succeeded nor given up —
    # e.g. EmailService scheduled a retry after a transient failure.  The
    # dispatcher leaves ``notification_delivery.status=pending`` so the row
    # will reflect the eventual outcome when the retry lands.
    pending: bool = False


class NotificationChannel(Protocol):
    """Protocol implemented by each notification transport (email, webhook)."""

    name: str

    async def send(
        self,
        subscription: "NotificationSubscription",
        context: Mapping[str, Any],
    ) -> ChannelResult:
        ...
