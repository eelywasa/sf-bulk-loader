"""EmailService — public entrypoint for all outbound email.

`EmailService.send(msg, category=...)` makes a synchronous first attempt and
returns the post-attempt EmailDelivery row.  Transient failures with remaining
retry budget schedule a background asyncio.create_task(_retry_loop(...)) and
return the row in 'pending' status.

Factory
-------
`build_email_service(backend_name, session_factory)` constructs an
EmailService from the configured backend name.  SMTP and SES backends are
added by SFBL-139 and SFBL-140 respectively — adding a new backend is a
one-line dict edit in the `_BACKENDS` registry below.

FastAPI dependency
------------------
`get_email_service()` is a FastAPI dependency that returns the module-level
singleton.  Call `init_email_service(settings, session_factory)` at app
startup (lifespan) to initialise it.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings as _settings
from app.models.email_delivery import EmailDelivery
from app.observability.events import EmailEvent, OutcomeCode
from app.observability.metrics import (
    _assert_email_reason,
    email_claim_lost_total,
    email_retry_total,
    email_send_duration_seconds,
    email_send_total,
)
from app.observability.sanitization import safe_exc_message
from app.observability.tracing import email_send_span
from app.services.email import delivery_log
from app.services.email.backends.base import BackendResult, EmailBackend
from app.services.email.backends.noop import NoopBackend
from app.services.email.backends.ses import SesBackend
from app.services.email.backends.smtp import SmtpBackend
from app.services.email.errors import EmailErrorReason
from app.services.email.message import EmailCategory, EmailMessage

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

logger = logging.getLogger(__name__)

# ── Backend registry ──────────────────────────────────────────────────────────
_BACKENDS: dict[str, EmailBackend] = {
    "noop": NoopBackend(),
    "smtp": SmtpBackend(),
    "ses": SesBackend(),
}


def build_email_service(
    backend_name: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> "EmailService":
    """Construct an EmailService for the given backend name.

    Raises KeyError if `backend_name` is not registered.  smtp and ses are
    added by SFBL-139 and SFBL-140 — adding a backend is a one-line dict edit
    in `_BACKENDS` above.
    """
    if backend_name not in _BACKENDS:
        raise KeyError(
            f"Unknown email backend {backend_name!r}. "
            f"Available: {sorted(_BACKENDS)}"
        )
    return EmailService(backend=_BACKENDS[backend_name], session_factory=session_factory)


# ── Module-level singleton ────────────────────────────────────────────────────

_email_service: "EmailService | None" = None


def init_email_service(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Initialise the module-level EmailService singleton.

    Must be called once during app startup (lifespan) after the DB connection
    is established.
    """
    global _email_service
    backend_name = _settings.email_backend or "noop"
    _email_service = build_email_service(backend_name, session_factory)
    logger.info(
        "Email service initialised",
        extra={
            "event_name": EmailEvent.SERVICE_INITIALISED,
            "outcome_code": OutcomeCode.OK,
            "backend": backend_name,
        },
    )


async def get_email_service() -> "EmailService":
    """FastAPI dependency that returns the module-level EmailService."""
    if _email_service is None:
        raise RuntimeError(
            "EmailService has not been initialised. "
            "Call init_email_service() in the app lifespan."
        )
    return _email_service


# ── Backoff helper ────────────────────────────────────────────────────────────


def _backoff_seconds(attempt_idx: int) -> float:
    """Compute capped exponential backoff with additive jitter.

    Formula from spec:
        raw = min(base * 2**attempt_idx, cap)
        jitter = uniform(0, base)
        delay = raw + jitter

    Additive jitter (not multiplicative) breaks thundering herds without
    pathologically long waits.
    """
    base = _settings.email_retry_backoff_seconds
    cap = _settings.email_retry_backoff_max_seconds
    raw = min(base * (2**attempt_idx), cap)
    jitter = random.uniform(0, base)
    return raw + jitter


# ── Outcome code helper ──────────────────────────────────────────────────────


def _email_outcome_code(backend_name: str, reason: "EmailErrorReason") -> str:
    """Map a backend name + error reason to an OutcomeCode string.

    Decision (SFBL-142):
    - Permanent auth errors → AUTH_ERROR (mirrors Salesforce auth outcome code)
    - Permanent config errors → EMAIL_CONFIG_ERROR
    - SMTP backend failures → EMAIL_SMTP_ERROR
    - SES backend failures → EMAIL_SES_ERROR
    - All other / noop → OK (noop never fails; unknown falls to FAILED)
    - Transient (retry scheduled) → OK (retry is not a terminal failure outcome)
    """
    if reason == EmailErrorReason.PERMANENT_AUTH:
        return OutcomeCode.AUTH_ERROR
    if reason == EmailErrorReason.PERMANENT_CONFIG:
        return OutcomeCode.EMAIL_CONFIG_ERROR
    if backend_name == "smtp":
        return OutcomeCode.EMAIL_SMTP_ERROR
    if backend_name == "ses":
        return OutcomeCode.EMAIL_SES_ERROR
    return OutcomeCode.FAILED


# ── EmailService ──────────────────────────────────────────────────────────────


class EmailService:
    """Public entrypoint for all outbound email.

    Each instance is bound to one backend and one session factory.  In
    production a single module-level singleton is used (init_email_service /
    get_email_service).  Tests may construct their own instances directly.
    """

    def __init__(
        self,
        backend: EmailBackend,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._backend = backend
        self._session_factory = session_factory

    async def send(
        self,
        msg: EmailMessage,
        *,
        category: EmailCategory,
        template: str | None = None,
        idempotency_key: str | None = None,
    ) -> EmailDelivery:
        """Deliver `msg` and return the post-first-attempt EmailDelivery row.

        Sending flow (per spec § Sending flow):
        1. If idempotency_key is set, return the existing row without sending.
        2. Insert a new email_delivery row in 'pending' (pre-claimed).
        3. Call backend.send(msg).
        4. On accepted=True: mark_sent (or mark_skipped for noop).
        5. On accepted=False, transient=True, attempts remaining: mark_failed
           with next_attempt_at, schedule _retry_loop.
        6. On accepted=False, permanent or exhausted: mark_failed terminally.
        """
        logger.info(
            "Email send requested",
            extra={
                "event_name": EmailEvent.SEND_REQUESTED,
                "outcome_code": OutcomeCode.OK,
                "backend": self._backend.name,
                "category": category.value,
            },
        )

        async with self._session_factory() as session:
            # Step 1 — idempotency check
            if idempotency_key is not None:
                existing = await delivery_log.get_by_idempotency_key(
                    session, idempotency_key
                )
                if existing is not None:
                    logger.debug(
                        "Idempotent send: returning existing row",
                        extra={
                            "delivery_id": existing.id,
                            "idempotency_key": idempotency_key,
                        },
                    )
                    return existing

            # Step 2 — insert pre-claimed row; step 3 — first attempt.
            # Both happen in the same session so the delivery object remains
            # persistent throughout. The session is committed inside each
            # delivery_log helper, so by the time we close it, all writes
            # are durable.
            delivery = await delivery_log.insert(
                session,
                msg=msg,
                backend_name=self._backend.name,
                category=category,
                template=template,
                idempotency_key=idempotency_key,
            )
            delivery = await self._attempt(session, delivery, msg)

        return delivery

    async def send_template(
        self,
        template_name: str,
        context: "Mapping[str, Any]",
        *,
        to: str,
        category: EmailCategory,
        reply_to: str | None = None,
        idempotency_key: str | None = None,
    ) -> "EmailDelivery":
        """Render *template_name* with *context* and deliver to *to*.

        Delegates template rendering to ``app.services.email.templates.render``
        and then calls ``self.send()``.  Raises ``EmailRenderError`` if the
        template is missing, invalid, or the rendered subject fails a safety
        check — before any delivery attempt is made.
        """
        from app.services.email.templates import render  # local import avoids circular dep at module level

        subject, text_body, html_body = render(template_name, context)
        msg = EmailMessage(
            to=to,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            reply_to=reply_to,
        )
        return await self.send(
            msg,
            category=category,
            template=template_name,
            idempotency_key=idempotency_key,
        )

    async def _attempt(
        self,
        session: AsyncSession,
        delivery: EmailDelivery,
        msg: EmailMessage,
    ) -> EmailDelivery:
        """Execute one send attempt and update the delivery row.

        Returns the refreshed delivery row.
        """
        import time

        backend_name = self._backend.name
        category_value = delivery.category
        template_value = delivery.template
        to_domain_value = delivery.to_domain
        attempt_number = delivery.attempts + 1  # 1-based for span/logs

        t_start = time.monotonic()

        with email_send_span(
            backend=backend_name,
            category=category_value,
            template=template_value,
            to_domain=to_domain_value,
            attempt=attempt_number,
        ) as span:
            result: BackendResult = await self._backend.send(msg)

            elapsed = time.monotonic() - t_start

            accepted = result.get("accepted", False)
            transient = result.get("transient", False)
            reason_raw = result.get("reason")
            error_detail = result.get("error_detail")
            provider_message_id = result.get("provider_message_id")

            if accepted:
                if backend_name == "noop":
                    await delivery_log.mark_skipped(session, delivery)
                    email_send_total.labels(
                        backend=backend_name,
                        category=category_value,
                        status="skipped",
                    ).inc()
                    email_send_duration_seconds.labels(
                        backend=backend_name,
                        category=category_value,
                    ).observe(elapsed)
                    logger.info(
                        "Email send skipped (noop backend)",
                        extra={
                            "event_name": EmailEvent.SEND_SKIPPED,
                            "outcome_code": OutcomeCode.OK,
                            "delivery_id": delivery.id,
                            "backend": backend_name,
                            "category": category_value,
                        },
                    )
                else:
                    await delivery_log.mark_sent(session, delivery, provider_message_id)
                    email_send_total.labels(
                        backend=backend_name,
                        category=category_value,
                        status="sent",
                    ).inc()
                    email_send_duration_seconds.labels(
                        backend=backend_name,
                        category=category_value,
                    ).observe(elapsed)
                    logger.info(
                        "Email send succeeded",
                        extra={
                            "event_name": EmailEvent.SEND_SUCCEEDED,
                            "outcome_code": OutcomeCode.OK,
                            "delivery_id": delivery.id,
                            "backend": backend_name,
                            "category": category_value,
                        },
                    )
                return delivery

            # Failure path
            reason: EmailErrorReason = (
                reason_raw if isinstance(reason_raw, EmailErrorReason)
                else EmailErrorReason.UNKNOWN
            )
            safe_detail = safe_exc_message(Exception(error_detail)) if error_detail else None

            # Record failure span attributes (span-only — provider_error_code never goes to metrics)
            span.set_attribute("email.reason", reason.value)
            if error_detail:
                span.set_attribute("email.provider_error_code", error_detail)

            # Map reason + backend to outcome code
            outcome_code = _email_outcome_code(backend_name, reason)

            email_send_duration_seconds.labels(
                backend=backend_name,
                category=category_value,
            ).observe(elapsed)

            attempts_after = delivery.attempts + 1
            can_retry = transient and reason.is_transient() and attempts_after < delivery.max_attempts

            if can_retry:
                delay = _backoff_seconds(delivery.attempts)
                next_at = datetime.fromtimestamp(
                    datetime.now(tz=timezone.utc).timestamp() + delay,
                    tz=timezone.utc,
                )
                await delivery_log.mark_failed(
                    session, delivery, reason, safe_detail, next_attempt_at=next_at
                )
                email_send_total.labels(
                    backend=backend_name,
                    category=category_value,
                    status="pending",
                ).inc()
                email_retry_total.labels(
                    backend=backend_name,
                    reason=_assert_email_reason(reason.value),
                ).inc()
                logger.warning(
                    "Email send failed (transient); retry scheduled",
                    extra={
                        "event_name": EmailEvent.SEND_RETRIED,
                        "outcome_code": OutcomeCode.OK,
                        "delivery_id": delivery.id,
                        "backend": backend_name,
                        "reason": reason.value,
                        "attempts": delivery.attempts,
                        "next_attempt_at": next_at.isoformat(),
                    },
                )
                asyncio.create_task(
                    self._retry_loop(delivery.id, msg),
                    name=f"email_retry_{delivery.id}",
                )
            else:
                await delivery_log.mark_failed(session, delivery, reason, safe_detail)
                email_send_total.labels(
                    backend=backend_name,
                    category=category_value,
                    status="failed",
                ).inc()
                logger.warning(
                    "Email send failed (terminal)",
                    extra={
                        "event_name": EmailEvent.SEND_FAILED,
                        "outcome_code": outcome_code,
                        "delivery_id": delivery.id,
                        "backend": backend_name,
                        "reason": reason.value,
                        "attempts": delivery.attempts,
                    },
                )

        return delivery

    async def _retry_loop(self, delivery_id: str, msg: EmailMessage) -> None:
        """Background retry task.

        Sleeps until next_attempt_at, then tries to claim the row.
        If the claim succeeds, re-attempts delivery.  If the claim fails
        (another worker has it, or it's already terminal), emits claim_lost
        and returns.
        """
        from sqlalchemy import select

        # Fetch current next_attempt_at to know how long to sleep
        async with self._session_factory() as session:
            row = await session.execute(
                select(EmailDelivery).where(EmailDelivery.id == delivery_id)
            )
            delivery = row.scalar_one_or_none()
            if delivery is None:
                return

            next_at = delivery.next_attempt_at
        # Session closed; now sleep outside any session context

        if next_at is not None:
            # SQLite may return naive datetimes even for timezone=True columns.
            # Ensure we compare aware to aware.
            if next_at.tzinfo is None:
                next_at = next_at.replace(tzinfo=timezone.utc)
            now = datetime.now(tz=timezone.utc)
            sleep_seconds = max(0.0, (next_at - now).total_seconds())
            await asyncio.sleep(sleep_seconds)

        # Try to claim and attempt in the same session so the delivery object
        # remains persistent throughout _attempt.
        async with self._session_factory() as session:
            claimed = await delivery_log.claim(
                session,
                delivery_id,
                delivery_log.WORKER_ID,
                _settings.email_claim_lease_seconds,
            )

            if claimed is None:
                email_claim_lost_total.labels(backend=self._backend.name).inc()
                logger.info(
                    "Email retry claim lost; another worker has the row",
                    extra={
                        "event_name": EmailEvent.SEND_CLAIM_LOST,
                        "outcome_code": OutcomeCode.OK,
                        "delivery_id": delivery_id,
                        "backend": self._backend.name,
                    },
                )
                return

            # Execute the retry attempt in the same session
            await self._attempt(session, claimed, msg)
