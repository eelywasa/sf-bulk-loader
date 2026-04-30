"""Invitation email dispatch helper (SFBL-202).

Sends the invite email via the EmailService singleton after a new InvitationToken
has been issued (POST /api/admin/users or POST /api/admin/users/{id}/resend-invite).

If the email backend is ``noop`` (desktop / smtp-not-configured), the send is
skipped silently — the raw token is still returned in the API response so the
admin can share the accept URL manually.
"""

from __future__ import annotations

import logging

from app.models.user import User
from app.observability.events import AuthEvent, OutcomeCode
from app.services.email.message import EmailCategory
from app.services.email.service import get_email_service

_log = logging.getLogger(__name__)

_TEMPLATE = "auth/invitation"


async def _get_frontend_base_url() -> str:
    """Resolve frontend_base_url from SettingsService.

    Mirrors the pattern in auth_reset.py / profile.py / notifications/channels/email.py
    so all email-emitting paths read the same DB-backed setting (registry key
    ``frontend_base_url``, env var ``FRONTEND_BASE_URL`` for first-boot seed).
    """
    from app.services.settings.service import settings_service as _svc
    if _svc is not None:
        return (await _svc.get("frontend_base_url")) or ""
    return ""


async def _build_accept_url(raw_token: str) -> str:
    """Construct the accept URL from the DB-backed frontend_base_url setting.

    Logs a warning when frontend_base_url is unconfigured — the admin caller
    still gets the raw token in the API response and can share the link
    manually, but the email body will contain a broken URL.
    """
    base_url = (await _get_frontend_base_url()).rstrip("/")
    if not base_url:
        _log.warning(
            "FRONTEND_BASE_URL not configured; invitation accept URL will be "
            "relative. Set FRONTEND_BASE_URL in .env (or via /settings/email "
            "post-boot) so invite emails contain a complete link.",
            extra={
                "event_name": AuthEvent.INVITATION_EMAIL_SENT,
                "outcome_code": OutcomeCode.CONFIGURATION_ERROR,
            },
        )
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

    accept_url = await _build_accept_url(raw_token)
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
