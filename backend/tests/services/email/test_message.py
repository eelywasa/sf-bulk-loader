"""Tests for EmailMessage validation and EmailCategory enum."""

import pytest

from app.services.email.message import EmailCategory, EmailMessage


class TestEmailCategory:
    def test_values(self):
        assert EmailCategory.NOTIFICATION.value == "notification"
        assert EmailCategory.AUTH.value == "auth"
        assert EmailCategory.SYSTEM.value == "system"

    def test_is_str_enum(self):
        assert isinstance(EmailCategory.NOTIFICATION, str)
        assert EmailCategory.AUTH == "auth"


class TestEmailMessageValidation:
    def _valid(self, **overrides) -> EmailMessage:
        kwargs = dict(
            to="user@example.com",
            subject="Hello",
            text_body="Body text",
        )
        kwargs.update(overrides)
        return EmailMessage(**kwargs)

    def test_valid_simple_address(self):
        msg = self._valid()
        assert msg.to == "user@example.com"

    def test_valid_display_name_address(self):
        msg = self._valid(to="Alice <alice@example.com>")
        assert msg.to == "Alice <alice@example.com>"

    def test_valid_with_html_body(self):
        msg = self._valid(html_body="<p>Hello</p>")
        assert msg.html_body == "<p>Hello</p>"

    def test_valid_with_reply_to(self):
        msg = self._valid(reply_to="noreply@example.com")
        assert msg.reply_to == "noreply@example.com"

    def test_valid_with_headers(self):
        msg = self._valid(headers={"X-Priority": "1"})
        assert msg.headers == {"X-Priority": "1"}

    # -- to field failures --

    def test_empty_to_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            self._valid(to="")

    def test_no_at_sign_raises(self):
        with pytest.raises(ValueError, match="not a valid RFC-5321"):
            self._valid(to="notanemail")

    def test_double_at_raises(self):
        with pytest.raises(ValueError, match="not a valid RFC-5321"):
            self._valid(to="a@@b.com")

    def test_no_domain_raises(self):
        with pytest.raises(ValueError, match="not a valid RFC-5321"):
            self._valid(to="user@")

    def test_multiple_recipients_in_to_raises(self):
        # A comma-separated list is not a valid single RFC-5321 address
        with pytest.raises(ValueError):
            self._valid(to="a@b.com,c@d.com")

    # -- subject failures --

    def test_empty_subject_raises(self):
        with pytest.raises(ValueError, match="subject must not be empty"):
            self._valid(subject="")

    # -- text_body failures --

    def test_empty_text_body_raises(self):
        with pytest.raises(ValueError, match="text_body must not be empty"):
            self._valid(text_body="")

    # -- frozen dataclass --

    def test_frozen(self):
        msg = self._valid()
        with pytest.raises((TypeError, AttributeError)):
            msg.subject = "different"  # type: ignore[misc]
