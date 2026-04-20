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
- strip_s3_query_string — strip query strings from s3:// URIs (presigned URL protection)
- safe_exc_message   — return a sanitized string representation of an exception
- safe_record_exception — record an exception on an OTel span without leaking
                          sensitive content via the exception message attribute
- sanitize_soql      — return a safe representation of a SOQL string for INFO-level
                        logging (length + hash + SELECT…FROM prefix only)
"""

from __future__ import annotations

import hashlib
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

# Regex that matches an S3 URI with a query string (e.g. a presigned URL).
# Captures the base URI (group 1) and strips everything from '?' onwards.
# This prevents presigned URL leakage (e.g. ?X-Amz-Signature=...) in log output.
_S3_PRESIGNED_PATTERN: re.Pattern[str] = re.compile(
    r"(s3://[^?\s]+)\?[^\s]*"
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


# ── S3 URI sanitization ───────────────────────────────────────────────────────


def strip_s3_query_string(value: str) -> str:
    """Strip query strings from ``s3://`` URIs to prevent presigned URL leakage.

    If *value* is an S3 URI containing a query string (e.g. a presigned URL
    with ``?X-Amz-Signature=...``), returns only the portion before the ``?``.
    Non-S3 strings and S3 URIs without a query string are returned unchanged.

    This function is safe to apply to any string — it only transforms values
    that match the presigned URI pattern.

    Examples::

        >>> strip_s3_query_string("s3://bucket/key?X-Amz-Signature=abc")
        "s3://bucket/key"
        >>> strip_s3_query_string("s3://bucket/key")
        "s3://bucket/key"
        >>> strip_s3_query_string("https://example.com/path?q=1")
        "https://example.com/path?q=1"
    """
    return _S3_PRESIGNED_PATTERN.sub(r"\1", value)


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
    # Strip query strings from s3:// URIs to prevent presigned URL leakage.
    raw = _S3_PRESIGNED_PATTERN.sub(r"\1", raw)
    return raw


# ── OTel span exception recording ────────────────────────────────────────────


# Regex that captures the "SELECT ... FROM <sobject>" prefix of a SOQL string,
# stopping before any WHERE / ORDER BY / LIMIT clause.
_SOQL_PREFIX_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)^(SELECT\s+.+?\s+FROM\s+\w+)",
    re.DOTALL,
)


def sanitize_soql(soql: str) -> str:
    """Return a safe representation of a SOQL string for INFO-level logging.

    SOQL ``WHERE`` clauses may contain field values that are quasi-identifiers
    (e.g. ``WHERE Email = 'alice@example.com'``). This function replaces the
    full SOQL with a non-reversible summary so that the query can be correlated
    with other log records without leaking record-level data.

    The returned string has the form::

        "<SELECT … FROM <sobject>> [len=<n> sha256=<first-8-hex>]"

    The ``SELECT ... FROM <sobject>`` prefix is safe because it contains only
    object/field names (low-cardinality metadata), not field values. If the
    prefix cannot be parsed, only the length and hash are emitted.

    At ``DEBUG`` level callers MAY log the full SOQL if the runtime log level
    permits it, because DEBUG output is never sent to production collectors by
    default.

    Args:
        soql: Raw SOQL query string.

    Returns:
        A sanitized, human-readable string safe for INFO-level log records and
        span attributes.

    Examples::

        >>> sanitize_soql("SELECT Id, Name FROM Account WHERE Email__c = 'x@y.com'")
        "SELECT Id, Name FROM Account [len=52 sha256=ab12cd34]"
        >>> sanitize_soql("SELECT Id FROM Contact LIMIT 100")
        "SELECT Id FROM Contact [len=31 sha256=cd56ef78]"
    """
    digest = hashlib.sha256(soql.encode()).hexdigest()[:8]
    length = len(soql)
    match = _SOQL_PREFIX_PATTERN.match(soql)
    prefix = match.group(1) if match else "SOQL"
    return f"{prefix} [len={length} sha256={digest}]"


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


# ── Webhook URL + recipient address sanitization (SFBL-180) ──────────────────


def sanitize_webhook_url(url: str) -> str:
    """Return *url* with query string and userinfo stripped.

    Webhook destinations for chat integrations (e.g. Slack) often carry a
    secret token in the path (``hooks.slack.com/services/T.../B.../XXX``).
    Stripping the query string and any ``user:pass@`` credentials keeps log
    output safe while preserving enough of the URL (scheme + host + path) to
    correlate failures to the right subscription.  The path IS retained
    because Slack-style tokens ride in the path and operators need to see
    which integration is misbehaving — the SFBL guidance only prohibits
    emitting the webhook token in plain text, and the URL is recorded on
    the subscription row in the database, not to telemetry.

    Fails open: if urlparse raises (malformed URL), returns ``"<invalid>"``.
    """
    from urllib.parse import urlparse, urlunparse

    try:
        parts = urlparse(url)
    except ValueError:
        return "<invalid>"
    # Rebuild netloc without userinfo
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    cleaned = parts._replace(netloc=host, query="", fragment="")
    try:
        return urlunparse(cleaned)
    except ValueError:
        return "<invalid>"


def redact_email_address(addr: str) -> str:
    """Obscure the local-part of an email address for telemetry.

    ``"alice@example.com"`` → ``"a***@example.com"``.  The first character
    of the local-part is preserved to aid debugging without disclosing the
    full identity; the domain is kept in full.  Strings without an ``@``
    return ``"<invalid>"``.
    """
    if "@" not in addr:
        return "<invalid>"
    local, _, domain = addr.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"
