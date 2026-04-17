"""NoopBackend — the default backend for desktop and unconfigured deployments.

Always reports accepted=True with no provider interaction. EmailService records
these deliveries as status='skipped' so operators can distinguish noop rows
from real sends in the delivery log.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.services.email.backends.base import BackendResult
from app.services.email.errors import EmailErrorReason
from app.services.email.message import EmailMessage


class NoopBackend:
    """No-operation email backend.

    Every send is immediately accepted. No network calls are made.
    Useful for desktop profiles, test environments, and cases where the
    operator has not configured an email provider.
    """

    name: ClassVar[str] = "noop"

    async def send(self, message: EmailMessage) -> BackendResult:
        """Accept the message without attempting delivery."""
        return BackendResult(
            accepted=True,
            provider_message_id=None,
            reason=None,
            error_detail=None,
            transient=False,
        )

    async def healthcheck(self) -> bool:
        """Noop backend is always healthy."""
        return True

    def classify(self, exc_or_code: Any) -> tuple[EmailErrorReason, bool]:
        """Noop backend never fails, so classify is unreachable in practice."""
        return (EmailErrorReason.UNKNOWN, False)
