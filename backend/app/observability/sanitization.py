"""Telemetry sanitization helpers — SFBL-60.

This module is the single authoritative definition of prohibited telemetry
content for the Salesforce Bulk Loader. All observability channels — logs,
traces, metrics labels, and error monitoring integrations — must comply with
these rules before emitting data.

Prohibited telemetry content
-----------------------------
The following must NEVER appear in any log record, span attribute, metric
label, or error-monitoring event:

- Salesforce access tokens (Bearer tokens returned by the OAuth flow)
- JWT assertions (compact base64url-encoded JWTs sent to Salesforce)
- RSA private keys (PEM format, stored Fernet-encrypted in the database)
- Fernet encryption keys (ENCRYPTION_KEY environment variable value)
- Passwords or secrets of any kind (including ``current_password``,
  ``new_password``, ``password``, ``hashed_password``)
- Raw single-use tokens: ``token``, ``raw_token`` — use ``token_hash``
  (SHA-256 digest) for telemetry correlation; the hash is safe to log
- Authorization HTTP request/response headers
- API keys
- Raw CSV row data (input or output)
- Secret environment variable values
- Reset-request or email-change email body content

Allowed telemetry content
--------------------------
The following IS safe to include in observability signals:

- Stable entity IDs: run_id, step_id, job_record_id, sf_job_id,
  load_plan_id, input_connection_id, request_id
- Salesforce object names and operation types (low-cardinality)
- HTTP status codes, method names, and URL paths (NOT query strings that
  might carry tokens)
- Record counts and byte sizes
- Outcome codes from app.observability.events.OutcomeCode
- Exception types and sanitized exception messages (no raw body content)
- Timestamps and durations

Public API
----------
- SCRUBBED_KEYS      — frozenset of lower-cased key names to redact
- scrub_dict         — redact sensitive keys in a flat dict
- scrub_headers      — redact sensitive HTTP headers (case-insensitive)
- safe_exc_message   — return a sanitized string representation of an exception
- safe_record_exception — record an exception on an OTel span without leaking
                          sensitive content via the exception message attribute
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Only imported for type-checking — opentelemetry is optional on the
    # desktop distribution which uses a slim requirements-desktop.txt.
    from opentelemetry.trace import Span

# ── Sensitive key registry ────────────────────────────────────────────────────

SCRUBBED_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "x-salesforce-token",
        "private_key",
        "private-key",
        "encryption_key",
        "encryption-key",
        "password",
        "secret",
        "token",
        "jwt",
        "api_key",
        "api-key",
        "access_token",
        "assertion",
        "bearer",
        # Email-specific keys (SFBL-142)
        "email_smtp_password",
        "ses_secret_access_key",
        "aws_secret_access_key",
        "to",
        "to_addr",
        "recipient",
        "recipients",
        "reset_url",
        "confirm_url",
        # Auth / password-reset + email-change fields (SFBL-151)
        # Raw password fields — plaintext credentials must never appear in telemetry.
        "current_password",
        "new_password",
        "hashed_password",
        # Raw tokens — single-use reset/verify tokens must not leak into logs.
        # Note: ``token_hash`` is a SHA-256 digest and IS acceptable in telemetry
        # (it cannot be reversed to recover the raw token).  Only the raw token
        # string itself is denied.
        "raw_token",
    }
)

# Regex that matches compact JWT patterns (three base64url segments separated
# by dots). Used to scrub token strings that may appear embedded in exception
# messages or error response bodies.
_JWT_PATTERN: re.Pattern[str] = re.compile(
    r"ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
)

# Regex that matches "Bearer <token>" patterns in strings.
_BEARER_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{10,}"
)

# Replacement sentinel used in redacted fields.
_REDACTED = "[REDACTED]"


# ── Dict / header scrubbing ───────────────────────────────────────────────────


def scrub_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *data* with sensitive keys replaced by [REDACTED].

    Key comparison is case-insensitive. Values are not inspected — only the
    key name drives redaction. Nested structures are not recursed into; call
    recursively if needed.
    """
    return {
        k: _REDACTED if k.lower() in SCRUBBED_KEYS else v for k, v in data.items()
    }


def scrub_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of *headers* with sensitive header values redacted.

    Identical to :func:`scrub_dict` but typed for header dicts where both
    keys and values are strings.
    """
    return {
        k: _REDACTED if k.lower() in SCRUBBED_KEYS else v for k, v in headers.items()
    }


# ── Exception message sanitization ───────────────────────────────────────────


def safe_exc_message(exc: BaseException) -> str:
    """Return a sanitized string representation of *exc*.

    Strips compact JWT tokens and Bearer credential patterns from the
    exception message so that exception text is safe to include in log
    records and span attributes.
    """
    raw = str(exc)
    # Remove JWT-shaped tokens.
    raw = _JWT_PATTERN.sub(_REDACTED, raw)
    # Remove "Bearer <token>" patterns.
    raw = _BEARER_PATTERN.sub(f"Bearer {_REDACTED}", raw)
    return raw


# ── OTel span exception recording ────────────────────────────────────────────


def safe_record_exception(span: Span, exc: BaseException) -> None:
    """Record *exc* on *span* without leaking sensitive content.

    OpenTelemetry's default ``span.record_exception(exc)`` attaches
    ``str(exc)`` verbatim as the ``exception.message`` span attribute. If the
    exception carries token or key material this would appear in traces.

    This wrapper records only:
    - ``exception.type``    — the class name (safe)
    - ``exception.message`` — the sanitized message from :func:`safe_exc_message`

    It intentionally omits the full ``exception.stacktrace`` attribute to
    avoid capturing any local variable values that might include secrets.
    Use standard exception logging (via the logging module with exc_info=True)
    for full tracebacks instead.

    opentelemetry is imported lazily so this module remains importable on the
    desktop distribution which does not include opentelemetry in its deps.
    """
    try:
        from opentelemetry.trace import NonRecordingSpan, StatusCode
    except ImportError:
        return

    if isinstance(span, NonRecordingSpan):
        return
    span.set_attribute("exception.type", type(exc).__name__)
    span.set_attribute("exception.message", safe_exc_message(exc))
    span.set_status(StatusCode.ERROR, safe_exc_message(exc))
