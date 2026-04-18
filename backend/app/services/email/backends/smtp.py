"""SmtpBackend — aiosmtplib-based EmailBackend implementation.

Reads configuration from `settings` at call time so send() picks up any
config changes without requiring a backend reinstantiation.

TLS modes (mutually exclusive in practice):
  - email_smtp_starttls=True (default)  → connect plaintext, then STARTTLS
  - email_smtp_use_tls=True             → implicit TLS from the start (port 465)
  - Both True                           → aiosmtplib will reject; error flows
                                          through to classify() as a config error

Per-send connection — no persistent pool. aiosmtplib is async and
lightweight enough that one connection per message is appropriate for
current transactional-only send volumes.
"""

from __future__ import annotations

import asyncio
import email.message
import logging
import socket
from typing import Any, ClassVar

import aiosmtplib

from app.config import settings
from app.observability.sanitization import safe_exc_message
from app.services.email.backends.base import BackendResult
from app.services.email.errors import EmailErrorReason
from app.services.email.message import EmailMessage

logger = logging.getLogger(__name__)

# ── SMTP response-code classification table ───────────────────────────────────
# Maps integer SMTP response codes to (EmailErrorReason, is_transient).
# Source: spec § Retry classification — SMTP half.
_CODE_TABLE: dict[int, tuple[EmailErrorReason, bool]] = {
    # Transient — provider unavailable
    421: (EmailErrorReason.TRANSIENT_PROVIDER_UNAVAILABLE, True),
    450: (EmailErrorReason.TRANSIENT_PROVIDER_UNAVAILABLE, True),
    451: (EmailErrorReason.TRANSIENT_PROVIDER_UNAVAILABLE, True),
    452: (EmailErrorReason.TRANSIENT_PROVIDER_UNAVAILABLE, True),
    # Permanent — auth
    535: (EmailErrorReason.PERMANENT_AUTH, False),
    # Permanent — reject
    550: (EmailErrorReason.PERMANENT_REJECT, False),
    551: (EmailErrorReason.PERMANENT_REJECT, False),
    553: (EmailErrorReason.PERMANENT_REJECT, False),
    554: (EmailErrorReason.PERMANENT_REJECT, False),
    # Permanent — address / envelope syntax
    501: (EmailErrorReason.PERMANENT_ADDRESS, False),
}


def classify(exc: BaseException) -> tuple[EmailErrorReason, str]:
    """Map an SMTP exception to (EmailErrorReason, raw_code_str).

    The raw_code_str is injected into BackendResult.error_detail as a prefix
    so operators can see the exact SMTP code without it leaking into metrics.

    Returns:
        (reason, raw_code_str) where raw_code_str is the SMTP code integer as
        a string (e.g. "421") or the exception class name for non-code errors.
    """
    # --- Envelope-level errors: check type first regardless of code ---
    # SMTPSenderRefused / SMTPRecipientsRefused carry a .code but the code is
    # the rejection code for the address, not for the message content.  Treat
    # them as permanent address errors regardless of their code value.
    if isinstance(exc, (aiosmtplib.SMTPRecipientsRefused, aiosmtplib.SMTPSenderRefused)):
        code = getattr(exc, "code", None)
        return EmailErrorReason.PERMANENT_ADDRESS, str(code) if code is not None else type(exc).__name__

    # --- Response-code exceptions (aiosmtplib attaches .code: int) ---
    code: int | None = getattr(exc, "code", None)

    if code is not None:
        entry = _CODE_TABLE.get(code)
        if entry is not None:
            return entry[0], str(code)
        logger.warning(
            "Unmapped SMTP response code; treating as UNKNOWN",
            extra={"smtp_code": code, "exc_type": type(exc).__name__},
        )
        return EmailErrorReason.UNKNOWN, str(code)

    # --- No code attribute — dispatch on exception type ---

    # aiosmtplib wraps timeouts in SMTPTimeoutError (subclass of OSError/TimeoutError),
    # so check for it before the broader OSError catch.  asyncio.TimeoutError may also
    # surface if the caller wraps with asyncio.wait_for().
    if isinstance(exc, aiosmtplib.SMTPTimeoutError):
        return EmailErrorReason.TRANSIENT_TIMEOUT, type(exc).__name__

    if isinstance(exc, asyncio.TimeoutError):
        return EmailErrorReason.TRANSIENT_TIMEOUT, type(exc).__name__

    if isinstance(exc, (ConnectionError, socket.gaierror, OSError)):
        # OSError is the base of both ConnectionError and socket.gaierror;
        # catching it here handles DNS-resolution failures and generic TCP errors.
        return EmailErrorReason.TRANSIENT_NETWORK, type(exc).__name__

    if isinstance(exc, aiosmtplib.SMTPException):
        logger.warning(
            "Unmapped aiosmtplib exception; treating as UNKNOWN",
            extra={"exc_type": type(exc).__name__, "exc_str": safe_exc_message(exc)},
        )
        return EmailErrorReason.UNKNOWN, type(exc).__name__

    logger.warning(
        "Unexpected exception type in SMTP classify; treating as UNKNOWN",
        extra={"exc_type": type(exc).__name__},
    )
    return EmailErrorReason.UNKNOWN, type(exc).__name__


def _build_mime(msg: EmailMessage) -> email.message.EmailMessage:
    """Build a stdlib EmailMessage MIME object from our EmailMessage."""
    mime = email.message.EmailMessage()

    # From — prefer "Display Name <addr>" if email_from_name is set
    from_address = settings.email_from_address or ""
    if settings.email_from_name and from_address:
        mime["From"] = f"{settings.email_from_name} <{from_address}>"
    else:
        mime["From"] = from_address

    mime["To"] = msg.to
    mime["Subject"] = msg.subject

    if msg.reply_to:
        mime["Reply-To"] = msg.reply_to

    # Body — text first; add HTML alternative if provided
    mime.set_content(msg.text_body)
    if msg.html_body:
        mime.add_alternative(msg.html_body, subtype="html")

    # Apply caller-supplied headers that aren't already set
    if msg.headers:
        existing = {k.lower() for k in mime.keys()}
        for key, value in msg.headers.items():
            if key.lower() not in existing:
                mime.add_header(key, value)

    return mime


class SmtpBackend:
    """aiosmtplib-based EmailBackend.

    Reads SMTP config from `settings` on every send() call.
    No persistent connection — a new SMTP session is opened per message.
    """

    name: ClassVar[str] = "smtp"

    async def send(self, msg: EmailMessage) -> BackendResult:
        """Deliver `msg` via SMTP.

        Always returns a BackendResult — never raises.  Provider exceptions
        are caught, classified, and embedded in the result.
        """
        mime = _build_mime(msg)
        timeout = settings.email_timeout_seconds

        try:
            async with aiosmtplib.SMTP(
                hostname=settings.email_smtp_host or "localhost",
                port=settings.email_smtp_port,
                username=settings.email_smtp_username or None,
                password=settings.email_smtp_password or None,
                use_tls=settings.email_smtp_use_tls,
                start_tls=settings.email_smtp_starttls if not settings.email_smtp_use_tls else False,
                timeout=timeout,
            ) as client:
                send_result = await client.send_message(mime)

        except (
            aiosmtplib.SMTPException,
            aiosmtplib.SMTPTimeoutError,
            asyncio.TimeoutError,
            OSError,
            ConnectionError,
            socket.gaierror,
        ) as exc:
            reason, raw_code = classify(exc)
            sanitised = safe_exc_message(exc)
            prefix = f"[SMTP:{raw_code}] "
            detail = (prefix + sanitised)[:500]
            return BackendResult(
                accepted=False,
                provider_message_id=None,
                reason=reason,
                error_detail=detail,
                transient=reason.is_transient(),
            )

        # On success — attempt to extract a provider message ID from the send result.
        # aiosmtplib.send_message() returns (dict[recipient, SMTPResponse], server_message_str).
        # The server-issued message-id (if any) may appear in the final server_message_str
        # but this is not standardised. None is the safe default per spec.
        provider_id: str | None = None
        if isinstance(send_result, tuple) and len(send_result) == 2:
            _recipient_responses, server_msg = send_result
            if server_msg:
                # Some servers embed "Ok <queue-id>" or "queued as <id>"
                parts = server_msg.strip().split()
                if len(parts) >= 2 and parts[0].lower() in ("ok", "queued"):
                    provider_id = parts[-1]

        return BackendResult(
            accepted=True,
            provider_message_id=provider_id,
            reason=None,
            error_detail=None,
            transient=False,
        )

    async def healthcheck(self) -> bool:
        """Return True if the SMTP host is TCP-reachable.

        Opens a raw TCP connection with a 2-second timeout (not email_timeout_seconds —
        healthcheck is budget-sensitive and must not hold up /dependencies probes).
        Does NOT perform STARTTLS or auth.
        """
        host = settings.email_smtp_host or "localhost"
        port = settings.email_smtp_port
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=2.0,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    def classify(self, exc_or_code: Any) -> tuple[EmailErrorReason, bool]:
        """Module-level classify() adapted to the Protocol signature.

        The Protocol expects (EmailErrorReason, is_transient: bool).
        Module-level classify() returns (reason, raw_code_str); this adapter
        strips the raw code and returns the transient flag instead.
        """
        if isinstance(exc_or_code, BaseException):
            reason, _ = classify(exc_or_code)
            return reason, reason.is_transient()
        # Integer code passed directly
        if isinstance(exc_or_code, int):
            entry = _CODE_TABLE.get(exc_or_code)
            if entry is not None:
                return entry
            logger.warning(
                "Unmapped SMTP response code in classify(); treating as UNKNOWN",
                extra={"smtp_code": exc_or_code},
            )
            return EmailErrorReason.UNKNOWN, False
        logger.warning(
            "Unexpected type in SmtpBackend.classify(); treating as UNKNOWN",
            extra={"type": type(exc_or_code).__name__},
        )
        return EmailErrorReason.UNKNOWN, False
