"""SES backend — AWS SES v2 SendEmail via aioboto3.

Uses the SES v2 (`ses-v2`) client.  Credentials come from the boto3 default
credential chain (IAM role in aws_hosted; env vars or ~/.aws/credentials in
self_hosted/dev).  No explicit access keys are accepted via config.

Region
------
If `settings.email_ses_region` is set it is passed as `region_name` to the
aioboto3 session; otherwise boto3 resolves the region via its default chain
(AWS_DEFAULT_REGION env var, ~/.aws/config, EC2/ECS metadata endpoint).

Configuration set
-----------------
When `settings.email_ses_configuration_set` is set it is included in every
SendEmail call as `ConfigurationSetName`.  When unset the kwarg is omitted
entirely — SES treats a missing key differently from an empty string.

healthcheck()
-------------
Calls SES v1 `GetSendQuota` (the equivalent of v2 `GetAccount` but broadly
available across all aioboto3 versions).  Result is cached for 60 seconds
at module level to avoid hammering SES on every `/dependencies` poll.

Error handling
--------------
All exceptions are caught inside `send()`.  `classify(exc)` maps
botocore ClientError codes and network/timeout errors to `EmailErrorReason`.
`error_detail` embeds the raw SES code and is clamped to 500 chars.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import Any, ClassVar

import aioboto3
from botocore.exceptions import ClientError, EndpointConnectionError

from app.observability.sanitization import safe_exc_message
from app.services.email.backends.base import BackendResult
from app.services.email.errors import EmailErrorReason
from app.services.email.message import EmailMessage

logger = logging.getLogger(__name__)

# ── healthcheck cache ─────────────────────────────────────────────────────────

_last_probe: tuple[float, bool] | None = None
_HEALTHCHECK_TTL_SECONDS = 60


# ── SES error code → EmailErrorReason mapping ─────────────────────────────────

# Transient codes — warrant a retry.
_TRANSIENT_THROTTLE_CODES = frozenset(
    {
        "Throttling",
        "ThrottlingException",
        "TooManyRequestsException",
    }
)

_TRANSIENT_UNAVAILABLE_CODES = frozenset(
    {
        "ServiceUnavailable",
        "InternalFailure",
        "InternalServerError",
    }
)

# Permanent codes — no retry.
_PERMANENT_REJECT_CODES = frozenset({"MessageRejected"})

_PERMANENT_CONFIG_CODES = frozenset(
    {
        "MailFromDomainNotVerifiedException",
        "ConfigurationSetDoesNotExist",
        "ConfigurationSetDoesNotExistException",
        "AccountSendingPausedException",
    }
)

_PERMANENT_AUTH_CODES = frozenset(
    {
        "AccessDeniedException",
        "UnrecognizedClientException",
        "InvalidClientTokenId",
    }
)

# InvalidParameterValue is permanent_address when it mentions an address,
# otherwise unknown.  Inspected in classify() below.
_INVALID_PARAM_CODE = "InvalidParameterValue"


def classify(exc: BaseException) -> tuple[EmailErrorReason, str]:
    """Map an exception to (EmailErrorReason, raw_code_str).

    For ``botocore.exceptions.ClientError`` the raw code is the SES error
    code string from ``exc.response["Error"]["Code"]``.

    For network / timeout exceptions a descriptive string is returned as the
    raw code.

    Unmapped codes fall to ``UNKNOWN`` with a warning log.
    """
    if isinstance(exc, ClientError):
        code: str = exc.response["Error"]["Code"]

        if code in _TRANSIENT_THROTTLE_CODES:
            return (EmailErrorReason.TRANSIENT_PROVIDER_THROTTLED, code)

        if code in _TRANSIENT_UNAVAILABLE_CODES:
            return (EmailErrorReason.TRANSIENT_PROVIDER_UNAVAILABLE, code)

        if code in _PERMANENT_REJECT_CODES:
            return (EmailErrorReason.PERMANENT_REJECT, code)

        if code in _PERMANENT_CONFIG_CODES:
            return (EmailErrorReason.PERMANENT_CONFIG, code)

        if code in _PERMANENT_AUTH_CODES:
            return (EmailErrorReason.PERMANENT_AUTH, code)

        if code == _INVALID_PARAM_CODE:
            # SES uses this code when the recipient address is invalid.
            msg_lower = str(exc).lower()
            if any(kw in msg_lower for kw in ("address", "email", "recipient", "from")):
                return (EmailErrorReason.PERMANENT_ADDRESS, code)
            # Otherwise fall through to UNKNOWN

        logger.warning(
            "SES ClientError code not in classify table; defaulting to UNKNOWN",
            extra={"ses_error_code": code, "exc_type": type(exc).__name__},
        )
        return (EmailErrorReason.UNKNOWN, code)

    # Network errors
    if isinstance(exc, (asyncio.TimeoutError,)):
        return (EmailErrorReason.TRANSIENT_TIMEOUT, "asyncio.TimeoutError")

    # botocore timeout exceptions
    exc_name = type(exc).__name__
    if exc_name in ("ReadTimeoutError", "ConnectTimeoutError"):
        return (EmailErrorReason.TRANSIENT_TIMEOUT, exc_name)

    # botocore endpoint / socket errors
    if isinstance(exc, EndpointConnectionError):
        return (EmailErrorReason.TRANSIENT_NETWORK, "EndpointConnectionError")

    if isinstance(exc, (ConnectionError, socket.gaierror, OSError)):
        return (EmailErrorReason.TRANSIENT_NETWORK, type(exc).__name__)

    logger.warning(
        "SES exception type not in classify table; defaulting to UNKNOWN",
        extra={"exc_type": exc_name},
    )
    return (EmailErrorReason.UNKNOWN, exc_name)


# ── SesBackend ────────────────────────────────────────────────────────────────


async def _get_svc():
    """Return the SettingsService singleton."""
    from app.services.settings.service import settings_service
    if settings_service is None:
        raise RuntimeError(
            "SettingsService has not been initialised. "
            "Call init_settings_service() in the app lifespan."
        )
    return settings_service


class SesBackend:
    """AWS SES v2 email backend.

    Sends via ``ses-v2`` ``SendEmail``.  Credentials come from the boto3
    default chain; no explicit keys are accepted.
    """

    name: ClassVar[str] = "ses"

    # ── internal helpers ──────────────────────────────────────────────────────

    async def _region_name(self) -> str | None:
        svc = await _get_svc()
        return (await svc.get("email_ses_region")) or None

    # ── send ──────────────────────────────────────────────────────────────────

    async def send(self, message: EmailMessage) -> BackendResult:
        """Deliver `message` via SES v2 SendEmail.

        Always returns a BackendResult; never raises.
        """
        try:
            svc = await _get_svc()
            from_address = (await svc.get("email_from_address")) or ""
            config_set = (await svc.get("email_ses_configuration_set")) or ""
            region = await self._region_name()

            session = aioboto3.Session()
            async with session.client(
                "sesv2",
                region_name=region,
            ) as client:
                kwargs: dict[str, Any] = {
                    "FromEmailAddress": from_address,
                    "Destination": {"ToAddresses": [message.to]},
                    "Content": {
                        "Simple": {
                            "Subject": {"Data": message.subject, "Charset": "UTF-8"},
                            "Body": self._build_body(message),
                        }
                    },
                    "ReplyToAddresses": [message.reply_to] if message.reply_to else [],
                }

                if config_set:
                    kwargs["ConfigurationSetName"] = config_set

                response = await client.send_email(**kwargs)

            provider_message_id: str | None = response.get("MessageId")
            return BackendResult(
                accepted=True,
                provider_message_id=provider_message_id,
                reason=None,
                error_detail=None,
                transient=False,
            )

        except Exception as exc:  # noqa: BLE001
            reason, raw_code = classify(exc)
            detail = f"[SES:{raw_code}] {safe_exc_message(exc)}"
            detail = detail[:500]
            return BackendResult(
                accepted=False,
                provider_message_id=None,
                reason=reason,
                error_detail=detail,
                transient=reason.is_transient(),
            )

    @staticmethod
    def _build_body(message: EmailMessage) -> dict[str, Any]:
        body: dict[str, Any] = {
            "Text": {"Data": message.text_body, "Charset": "UTF-8"},
        }
        if message.html_body:
            body["Html"] = {"Data": message.html_body, "Charset": "UTF-8"}
        return body

    # ── healthcheck ───────────────────────────────────────────────────────────

    async def healthcheck(self) -> bool:
        """Return True if SES is reachable.

        Result is cached for 60 seconds to avoid unnecessary API calls from
        the ``/dependencies`` probe.
        """
        global _last_probe

        now = time.monotonic()
        if _last_probe is not None and now - _last_probe[0] < _HEALTHCHECK_TTL_SECONDS:
            return _last_probe[1]

        ok = await self._probe()
        _last_probe = (now, ok)
        return ok

    async def _probe(self) -> bool:
        """Attempt a lightweight SES API call.

        Uses v2 ``GetAccount`` if available on the aioboto3 client model,
        falls back to v1 ``GetSendQuota`` on an ``ses`` (not ``ses-v2``)
        client.  Either proves connectivity and credential validity.
        """
        try:
            region = await self._region_name()
            session = aioboto3.Session()
            # Try SES v2 GetAccount first (available in aioboto3 >= 12 with
            # recent botocore models).
            try:
                async with session.client(
                    "sesv2",
                    region_name=region,
                ) as client:
                    await client.get_account()
                return True
            except AttributeError:
                # sesv2 client model doesn't expose get_account in this version;
                # fall back to v1 GetSendQuota.
                pass
            async with session.client(
                "ses",
                region_name=region,
            ) as client:
                await client.get_send_quota()
            return True

        except Exception:  # noqa: BLE001
            logger.warning(
                "SES healthcheck failed",
                exc_info=True,
                extra={"event_name": "email.healthcheck.failed", "backend": "ses"},
            )
            return False

    # ── classify (instance proxy) ─────────────────────────────────────────────

    def classify(self, exc_or_code: Any) -> tuple[EmailErrorReason, bool]:
        """Instance proxy to the module-level classify() function.

        Returns ``(EmailErrorReason, is_transient)`` as required by the
        ``EmailBackend`` Protocol.
        """
        if isinstance(exc_or_code, BaseException):
            reason, _ = classify(exc_or_code)
        else:
            # Treat string codes as a synthetic ClientError-like lookup
            reason, _ = classify(
                _make_client_error(str(exc_or_code))
            )
        return (reason, reason.is_transient())


def _make_client_error(code: str) -> ClientError:
    """Construct a minimal ClientError for a given SES error code string."""
    return ClientError(
        error_response={"Error": {"Code": code, "Message": code}},
        operation_name="SendEmail",
    )
