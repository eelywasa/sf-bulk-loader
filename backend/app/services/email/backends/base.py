"""Email backend Protocol and BackendResult TypedDict.

Every backend (noop, smtp, ses) implements `EmailBackend`.  `EmailService`
only reads `BackendResult.accepted`, `BackendResult.reason`, and
`BackendResult.transient` — it never inspects raw provider exceptions or codes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from typing_extensions import TypedDict

if TYPE_CHECKING:
    from app.services.email.errors import EmailErrorReason
    from app.services.email.message import EmailMessage


class BackendResult(TypedDict, total=False):
    """Result returned by `EmailBackend.send`.

    All fields are optional (total=False) so backends return only what is
    relevant.  `EmailService` must handle missing keys gracefully via `.get()`.
    """

    accepted: bool                    # True = provider accepted the message
    provider_message_id: str | None   # Provider's message-id on success
    reason: "EmailErrorReason | None" # Normalised classification on failure
    error_detail: str | None          # Sanitised raw provider code/message
    transient: bool                   # True = failure is worth retrying


class EmailBackend(Protocol):
    """Protocol every email backend must satisfy.

    Implementations are responsible for mapping their own provider-specific
    exceptions and codes to `EmailErrorReason` via a `classify` method, then
    populating `BackendResult` accordingly.
    """

    name: ClassVar[str]  # "noop" | "smtp" | "ses"

    async def send(self, message: "EmailMessage") -> BackendResult:
        """Attempt to deliver `message`.

        Must always return a `BackendResult` — never raise.  Any provider
        exception must be caught and classified inside the backend.
        """
        ...

    async def healthcheck(self) -> bool:
        """Return True if the backend is operational.

        Used by the `/dependencies` probe (SFBL-142).  Must not raise.
        """
        ...

    def classify(self, exc_or_code: Any) -> tuple["EmailErrorReason", bool]:
        """Map a provider exception or error code to (EmailErrorReason, is_transient).

        Unmapped values must return (EmailErrorReason.UNKNOWN, False).
        Backends must log a warning with the raw code so it can be added to
        the classification table in a follow-up.
        """
        ...
