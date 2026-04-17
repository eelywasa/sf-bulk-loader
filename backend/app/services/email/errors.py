"""Email service error hierarchy.

`EmailError`       — base for all email-service failures.
`EmailRenderError` — template rendering / validation failure with a stable
                     machine-readable code.
`EmailErrorReason` — fixed enum that classifies backend-level send failures.
                     Nine values; never extended with raw provider codes.
"""

from __future__ import annotations

import enum


class EmailError(Exception):
    """Base class for all email-service failures."""


class EmailRenderError(EmailError):
    """Raised when a template is invalid, missing context, or produces an
    unsafe subject.

    The exception message is the stable `code` only — the offending value
    must never enter the message, logs, or delivery log.

    Stable code values:
        MISSING_REQUIRED_CONTEXT
        UNKNOWN_CONTEXT_KEY
        SUBJECT_REFERENCES_DISALLOWED_KEY
        SUBJECT_CONTAINS_URL
        SUBJECT_CONTAINS_OPAQUE_TOKEN
        SUBJECT_CONTAINS_CONTROL_CHARS
        SUBJECT_TOO_LONG
        AUTH_TEMPLATE_DYNAMIC_SUBJECT
        TEMPLATE_UNAVAILABLE
    """

    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail  # safe description — never the offending value
        super().__init__(code)


_TRANSIENT_REASONS = frozenset(
    {
        "transient_network",
        "transient_timeout",
        "transient_provider_throttled",
        "transient_provider_unavailable",
    }
)


class EmailErrorReason(str, enum.Enum):
    """Fixed classification of backend-level send failures.

    Used as the `reason` label on metrics and as the normalised value stored
    in `email_delivery.last_error_code`. Backends map provider-specific codes
    to this enum via their `classify()` method; raw provider codes never reach
    metric labels.
    """

    TRANSIENT_NETWORK = "transient_network"
    TRANSIENT_TIMEOUT = "transient_timeout"
    TRANSIENT_PROVIDER_THROTTLED = "transient_provider_throttled"
    TRANSIENT_PROVIDER_UNAVAILABLE = "transient_provider_unavailable"
    PERMANENT_REJECT = "permanent_reject"
    PERMANENT_AUTH = "permanent_auth"
    PERMANENT_CONFIG = "permanent_config"
    PERMANENT_ADDRESS = "permanent_address"
    UNKNOWN = "unknown"

    def is_transient(self) -> bool:
        """Return True if this reason warrants a retry attempt."""
        return self.value in _TRANSIENT_REASONS
