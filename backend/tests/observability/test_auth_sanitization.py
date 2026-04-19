"""Tests that auth-related sensitive fields are correctly scrubbed — SFBL-151.

The deny-list test asserts that a deliberately noisy request body going through
the error path does NOT leak any prohibited fields into the scrubbed output.

Also covers the documented ``token_hash`` exception: SHA-256 digests are safe
for telemetry and must NOT be scrubbed.
"""

from __future__ import annotations

import pytest

from app.observability.sanitization import SCRUBBED_KEYS, scrub_dict


# ── Deny-list presence ────────────────────────────────────────────────────────


class TestAuthScrubKeys:
    """Each auth-specific key must exist in SCRUBBED_KEYS."""

    def test_current_password_denied(self) -> None:
        assert "current_password" in SCRUBBED_KEYS

    def test_new_password_denied(self) -> None:
        assert "new_password" in SCRUBBED_KEYS

    def test_password_denied(self) -> None:
        assert "password" in SCRUBBED_KEYS

    def test_hashed_password_denied(self) -> None:
        assert "hashed_password" in SCRUBBED_KEYS

    def test_raw_token_denied(self) -> None:
        assert "raw_token" in SCRUBBED_KEYS

    def test_token_denied(self) -> None:
        # The generic ``token`` key is denied — use ``token_hash`` for safe IDs.
        assert "token" in SCRUBBED_KEYS

    def test_all_deny_keys_are_lowercase(self) -> None:
        for key in SCRUBBED_KEYS:
            assert key == key.lower(), f"SCRUBBED_KEYS entry must be lowercase: {key!r}"


# ── token_hash is NOT denied ──────────────────────────────────────────────────


class TestTokenHashAllowed:
    """``token_hash`` (SHA-256 digest) is safe for telemetry and must not be scrubbed."""

    def test_token_hash_not_in_scrubbed_keys(self) -> None:
        assert "token_hash" not in SCRUBBED_KEYS

    def test_scrub_dict_passes_through_token_hash(self) -> None:
        sha256_digest = "a" * 64  # 64 hex chars = 32-byte SHA-256 digest
        result = scrub_dict({"token_hash": sha256_digest})
        assert result["token_hash"] == sha256_digest, (
            "token_hash (SHA-256 digest) must NOT be redacted — it is safe for telemetry"
        )


# ── Noisy request body scrubbing ──────────────────────────────────────────────


_NOISY_REQUEST_BODY: dict = {
    # Raw credentials — must be denied
    "current_password": "MyS3cr3tP@ssw0rd!",
    "new_password": "AnotherS3cr3t!99",
    "password": "hunter2",
    "hashed_password": "$2b$12$examplehashvalue",
    # Raw tokens — must be denied
    "token": "abc123rawtoken",
    "raw_token": "xyz789rawresettoken",
    # Safe fields — must pass through
    "token_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "token_id": "42",
    "user_id": "usr-123",
    "email_hash": "hashofemailnotrealaddress",
    "outcome_code": "sent",
    "event_name": "auth.password.reset.requested",
}

_PROHIBITED_VALUES = {
    "MyS3cr3tP@ssw0rd!",
    "AnotherS3cr3t!99",
    "hunter2",
    "$2b$12$examplehashvalue",
    "abc123rawtoken",
    "xyz789rawresettoken",
}

_SAFE_VALUES = {
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "42",
    "usr-123",
    "hashofemailnotrealaddress",
    "sent",
    "auth.password.reset.requested",
}


class TestNoisyBodyScrubbing:
    """A request body containing all auth-sensitive fields must be fully sanitised."""

    def setup_method(self) -> None:
        self.scrubbed = scrub_dict(_NOISY_REQUEST_BODY)

    def test_no_prohibited_values_leak(self) -> None:
        scrubbed_values = set(self.scrubbed.values())
        leaked = _PROHIBITED_VALUES & scrubbed_values
        assert not leaked, (
            f"Prohibited value(s) leaked into scrubbed output: {leaked!r}"
        )

    def test_current_password_redacted(self) -> None:
        assert self.scrubbed["current_password"] == "[REDACTED]"

    def test_new_password_redacted(self) -> None:
        assert self.scrubbed["new_password"] == "[REDACTED]"

    def test_password_redacted(self) -> None:
        assert self.scrubbed["password"] == "[REDACTED]"

    def test_hashed_password_redacted(self) -> None:
        assert self.scrubbed["hashed_password"] == "[REDACTED]"

    def test_token_redacted(self) -> None:
        assert self.scrubbed["token"] == "[REDACTED]"

    def test_raw_token_redacted(self) -> None:
        assert self.scrubbed["raw_token"] == "[REDACTED]"

    def test_safe_fields_pass_through(self) -> None:
        assert self.scrubbed["token_hash"] == _NOISY_REQUEST_BODY["token_hash"]
        assert self.scrubbed["token_id"] == "42"
        assert self.scrubbed["user_id"] == "usr-123"
        assert self.scrubbed["email_hash"] == "hashofemailnotrealaddress"
        assert self.scrubbed["outcome_code"] == "sent"
        assert self.scrubbed["event_name"] == "auth.password.reset.requested"

    def test_original_dict_not_mutated(self) -> None:
        # scrub_dict must return a new dict, not modify in-place
        assert _NOISY_REQUEST_BODY["current_password"] == "MyS3cr3tP@ssw0rd!"
