"""Tests for SFBL-180 telemetry sanitisers."""

from __future__ import annotations

import pytest

from app.observability.sanitization import (
    redact_email_address,
    sanitize_webhook_url,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "https://hooks.slack.com/services/T1/B2/XYZ?token=secret",
            "https://hooks.slack.com/services/T1/B2/XYZ",
        ),
        (
            "https://user:pass@example.com/webhook",
            "https://example.com/webhook",
        ),
        (
            "https://example.com:8443/path#frag",
            "https://example.com:8443/path",
        ),
    ],
)
def test_sanitize_webhook_url(raw, expected):
    assert sanitize_webhook_url(raw) == expected


def test_sanitize_webhook_url_invalid():
    # urlparse accepts almost anything — so a genuinely unparseable string
    # still returns a best-effort cleaned form; we only assert the query
    # is stripped.
    assert "?" not in sanitize_webhook_url("not a url?x=1")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("alice@example.com", "a***@example.com"),
        ("bob+filter@corp.co", "b***@corp.co"),
        ("@example.com", "***@example.com"),
        ("nope", "<invalid>"),
    ],
)
def test_redact_email_address(raw, expected):
    assert redact_email_address(raw) == expected
