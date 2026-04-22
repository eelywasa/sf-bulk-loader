"""Invitation email dispatch helper (SFBL-202).

Sends the invite email via the EmailService singleton after a new InvitationToken
has been issued (POST /api/admin/users or POST /api/admin/users/{id}/resend-invite).

If the email backend is ``noop`` (desktop / smtp-not-configured), the send is
skipped silently — the raw token is still returned in the API response so the
admin can share the accept URL manually.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.models.user import User
from app.observability.events import AuthEvent, OutcomeCode
from app.services.email.message import EmailCategory
from app.services.email.service import get_email_service

_log = logging.getLogger(__name__)

_TEMPLATE = "auth/invitation"


def _build_accept_url(raw_token: str) -> str:
    """Construct the accept URL from BASE_URL config."""
    base_url = (settings.base_url or "").rstrip("/")
    return f"{base_url}/invite/accept?token={raw_token}"


async def send_invitation_email(
    user: User,
    raw_token: str,
    expires_in_hours: int,
) -> None:
    """Send an invitation email to *user*.

    Silently skips if the email backend is noop (no SMTP / SES configured).
    All exceptions from the email service are caught and logged — a failed
    invitation email must never abort the invite API response because the raw
    token is already returned to the admin caller.
    """
    try:
        email_svc = await get_email_service()
    except RuntimeError:
        _log.warning(
            "Email service not initialised — skipping invitation email",
            extra={
                "event_name": AuthEvent.INVITATION_EMAIL_SENT,
                "outcome_code": OutcomeCode.INVITATION_EMAIL_SKIPPED,
                "user_id": user.id,
            },
        )
        return

    # Skip for noop backend (desktop / unconfigured SMTP)
    if email_svc._backend.name == "noop":
        _log.info(
            "Invitation email skipped (noop backend)",
            extra={
                "event_name": AuthEvent.INVITATION_EMAIL_SENT,
                "outcome_code": OutcomeCode.INVITATION_EMAIL_SKIPPED,
                "user_id": user.id,
            },
        )
        return

    accept_url = _build_accept_url(raw_token)
    display_name = user.display_name or user.email

    try:
        await email_svc.send_template(
            _TEMPLATE,
            {
                "user_display_name": display_name,
                "accept_url": accept_url,
                "expires_in_hours": expires_in_hours,
            },
            to=user.email,
            category=EmailCategory.AUTH,
            idempotency_key=None,
        )
        _log.info(
            "Invitation email sent",
            extra={
                "event_name": AuthEvent.INVITATION_EMAIL_SENT,
                "outcome_code": OutcomeCode.INVITATION_EMAIL_SENT,
                "user_id": user.id,
            },
        )
    except Exception as exc:
        _log.error(
            "Failed to send invitation email",
            exc_info=exc,
            extra={
                "event_name": AuthEvent.INVITATION_EMAIL_SENT,
                "outcome_code": OutcomeCode.FAILED,
                "user_id": user.id,
            },
        )
        # Do NOT re-raise — the raw token is already returned to the admin;
        # the email failure is a side-effect, not a fatal error.
