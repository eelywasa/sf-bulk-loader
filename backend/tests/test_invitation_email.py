"""Tests for invitation email dispatch (SFBL-202).

Coverage
--------
- Email sent when backend is configured (non-noop)
- Email skipped when backend is noop
- Email body contains the accept URL with the raw token
- send_invitation_email does NOT raise when email service fails
- display_name falls back to email when not set
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.user import User


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_user(email: str = "invited@example.com", display_name: str | None = None) -> User:
    return User(
        id=str(uuid.uuid4()),
        email=email,
        display_name=display_name,
        status="invited",
    )


def _make_mock_service(backend_name: str) -> tuple[AsyncMock, MagicMock]:
    """Return (mock_get_email_service, mock_svc) for the given backend name."""
    mock_backend = MagicMock()
    mock_backend.name = backend_name

    mock_svc = MagicMock()
    mock_svc._backend = mock_backend
    mock_svc.send_template = AsyncMock()

    mock_get = AsyncMock(return_value=mock_svc)
    return mock_get, mock_svc


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestSendInvitationEmail:
    def test_email_sent_when_backend_is_smtp(self):
        """send_invitation_email dispatches via send_template when backend != noop."""
        user = _make_user("smtp-user@example.com", "SMTP User")
        raw_token = "testrawtoken123"

        mock_get, mock_svc = _make_mock_service("smtp")

        async def _test():
            with patch("app.services.invitation_email.get_email_service", mock_get):
                from app.services.invitation_email import send_invitation_email
                await send_invitation_email(user, raw_token, expires_in_hours=24)

        _run(_test())

        mock_svc.send_template.assert_called_once()
        call_args = mock_svc.send_template.call_args
        # Positional: template_name, context
        assert call_args[0][0] == "auth/invitation"
        ctx = call_args[0][1]
        assert "accept_url" in ctx
        assert raw_token in ctx["accept_url"]
        assert ctx["user_display_name"] == "SMTP User"
        assert ctx["expires_in_hours"] == 24
        assert call_args[1]["to"] == "smtp-user@example.com"

    def test_email_skipped_when_backend_is_noop(self):
        """send_invitation_email does NOT call send_template for the noop backend."""
        user = _make_user("noop-user@example.com")
        raw_token = "testrawtoken456"

        mock_get, mock_svc = _make_mock_service("noop")

        async def _test():
            with patch("app.services.invitation_email.get_email_service", mock_get):
                from app.services.invitation_email import send_invitation_email
                await send_invitation_email(user, raw_token, expires_in_hours=24)

        _run(_test())
        mock_svc.send_template.assert_not_called()

    def test_email_skipped_when_service_not_initialised(self):
        """send_invitation_email silently skips if EmailService not yet initialised."""
        user = _make_user("uninit@example.com")

        mock_get = AsyncMock(side_effect=RuntimeError("Not initialised"))

        async def _test():
            with patch("app.services.invitation_email.get_email_service", mock_get):
                from app.services.invitation_email import send_invitation_email
                # Must not raise
                await send_invitation_email(user, "tok", expires_in_hours=24)

        _run(_test())  # no exception

    def test_email_failure_does_not_propagate(self):
        """Even if send_template throws, send_invitation_email swallows the error."""
        user = _make_user("fail-email@example.com")

        mock_get, mock_svc = _make_mock_service("smtp")
        mock_svc.send_template = AsyncMock(side_effect=Exception("SMTP connection refused"))

        async def _test():
            with patch("app.services.invitation_email.get_email_service", mock_get):
                from app.services.invitation_email import send_invitation_email
                # Must not raise
                await send_invitation_email(user, "tok", expires_in_hours=24)

        _run(_test())  # no exception

    def test_accept_url_contains_token(self):
        """The accept URL built from _build_accept_url includes the raw token."""
        from app.services.invitation_email import _build_accept_url

        url = _build_accept_url("abc123")
        assert "abc123" in url
        assert "/invite/accept" in url

    def test_display_name_falls_back_to_email(self):
        """If user has no display_name, the email address is used as greeting."""
        user = _make_user("nodisplay@example.com", display_name=None)
        raw_token = "tokfallback"

        mock_get, mock_svc = _make_mock_service("smtp")

        async def _test():
            with patch("app.services.invitation_email.get_email_service", mock_get):
                from app.services.invitation_email import send_invitation_email
                await send_invitation_email(user, raw_token, expires_in_hours=24)

        _run(_test())
        ctx = mock_svc.send_template.call_args[0][1]
        assert ctx["user_display_name"] == "nodisplay@example.com"
