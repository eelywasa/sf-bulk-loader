"""Tests for SFBL-180 telemetry sanitisers."""

from __future__ import annotations

import pytest

from app.observability.sanitization import (
    redact_email_address,
    sanitize_webhook_url,
)


@pytest.mark.parametrize(
    "raw,host_part",
    [
        (
            "https://hooks.slack.com/services/T1/B2/XYZ?token=secret",
            "hooks.slack.com",
        ),
        (
            "https://user:pass@example.com/webhook",
            "example.com",
        ),
        (
            "https://example.com:8443/path/segment/secret#frag",
            "example.com:8443",
        ),
    ],
)
def test_sanitize_webhook_url_strips_path_and_userinfo(raw, host_part):
    out = sanitize_webhook_url(raw)
    assert host_part in out
    # Secret path segments must not leak into telemetry.
    assert "XYZ" not in out
    assert "secret" not in out
    assert "pass" not in out
    # Query + userinfo must be gone.
    assert "?" not in out
    assert "@" not in out
    # Must carry a short non-reversible fingerprint so operators can still
    # cross-reference two log lines as the same subscription.
    assert "sha256=" in out


def test_sanitize_webhook_url_invalid():
    assert sanitize_webhook_url("") == "<invalid>"


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
