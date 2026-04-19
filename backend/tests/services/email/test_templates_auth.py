"""Rendering tests for auth email templates (SFBL-147).

Covers:
- auth/password_reset: HTML contains reset_url and expires_in_minutes; plain-text non-empty.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet

# Env vars must be set before importing any app modules
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("JWT_SECRET_KEY", "auth-template-test-jwt-secret")
os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "Test-Admin-P4ss!")

from app.services.email.templates import render  # noqa: E402


_PASSWORD_RESET_CONTEXT = {
    "user_display_name": "Alice",
    "reset_url": "http://localhost/reset-password/abc123token",
    "expires_in_minutes": 15,
}


class TestPasswordResetTemplate:
    """Rendering tests for auth/password_reset."""

    def test_html_contains_reset_url(self):
        _subject, _text, html = render("auth/password_reset", _PASSWORD_RESET_CONTEXT)
        assert html is not None
        assert "http://localhost/reset-password/abc123token" in html

    def test_html_contains_expires_in_minutes(self):
        _subject, _text, html = render("auth/password_reset", _PASSWORD_RESET_CONTEXT)
        assert html is not None
        assert "15" in html

    def test_plain_text_non_empty(self):
        _subject, text, _html = render("auth/password_reset", _PASSWORD_RESET_CONTEXT)
        assert text.strip() != ""

    def test_plain_text_contains_reset_url(self):
        _subject, text, _html = render("auth/password_reset", _PASSWORD_RESET_CONTEXT)
        assert "http://localhost/reset-password/abc123token" in text

    def test_subject_is_correct(self):
        subject, _text, _html = render("auth/password_reset", _PASSWORD_RESET_CONTEXT)
        assert subject == "Reset your Salesforce Bulk Loader password"

    def test_html_greets_by_display_name(self):
        _subject, _text, html = render("auth/password_reset", _PASSWORD_RESET_CONTEXT)
        assert html is not None
        assert "Alice" in html
