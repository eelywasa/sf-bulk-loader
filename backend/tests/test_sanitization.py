"""Tests for app.observability.sanitization — SFBL-60."""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from app.observability.sanitization import (
    SCRUBBED_KEYS,
    safe_exc_message,
    safe_record_exception,
    scrub_dict,
    scrub_headers,
    strip_s3_query_string,
)


# ── SCRUBBED_KEYS coverage ────────────────────────────────────────────────────


class TestScrubbedKeys:
    def test_contains_authorization(self):
        assert "authorization" in SCRUBBED_KEYS

    def test_contains_token(self):
        assert "token" in SCRUBBED_KEYS

    def test_contains_access_token(self):
        assert "access_token" in SCRUBBED_KEYS

    def test_contains_private_key(self):
        assert "private_key" in SCRUBBED_KEYS

    def test_contains_password(self):
        assert "password" in SCRUBBED_KEYS

    def test_contains_jwt(self):
        assert "jwt" in SCRUBBED_KEYS

    def test_contains_encryption_key(self):
        assert "encryption_key" in SCRUBBED_KEYS

    def test_contains_api_key(self):
        assert "api_key" in SCRUBBED_KEYS

    def test_contains_secret(self):
        assert "secret" in SCRUBBED_KEYS

    def test_all_keys_lowercase(self):
        for key in SCRUBBED_KEYS:
            assert key == key.lower(), f"SCRUBBED_KEYS must be lowercase: {key!r}"


# ── scrub_dict ────────────────────────────────────────────────────────────────


class TestScrubDict:
    def test_redacts_sensitive_key(self):
        result = scrub_dict({"authorization": "Bearer abc123"})
        assert result["authorization"] == "[REDACTED]"

    def test_passes_through_safe_key(self):
        result = scrub_dict({"run_id": "abc", "status": "ok"})
        assert result["run_id"] == "abc"
        assert result["status"] == "ok"

    def test_redacts_token(self):
        result = scrub_dict({"token": "some-token-value"})
        assert result["token"] == "[REDACTED]"

    def test_redacts_access_token(self):
        result = scrub_dict({"access_token": "00D..."})
        assert result["access_token"] == "[REDACTED]"

    def test_case_insensitive_key_match(self):
        result = scrub_dict({"Authorization": "Bearer x", "PRIVATE_KEY": "pem"})
        assert result["Authorization"] == "[REDACTED]"
        assert result["PRIVATE_KEY"] == "[REDACTED]"

    def test_mixed_dict(self):
        data = {
            "run_id": "run-1",
            "password": "hunter2",
            "object_name": "Account",
            "api_key": "key-abc",
        }
        result = scrub_dict(data)
        assert result["run_id"] == "run-1"
        assert result["object_name"] == "Account"
        assert result["password"] == "[REDACTED]"
        assert result["api_key"] == "[REDACTED]"

    def test_empty_dict(self):
        assert scrub_dict({}) == {}

    def test_does_not_mutate_original(self):
        original = {"token": "secret", "safe": "value"}
        scrub_dict(original)
        assert original["token"] == "secret"


# ── scrub_headers ─────────────────────────────────────────────────────────────


class TestScrubHeaders:
    def test_redacts_authorization_header(self):
        result = scrub_headers({"Authorization": "Bearer eyJ..."})
        assert result["Authorization"] == "[REDACTED]"

    def test_passes_through_safe_header(self):
        result = scrub_headers({"Content-Type": "application/json"})
        assert result["Content-Type"] == "application/json"

    def test_case_insensitive(self):
        result = scrub_headers({"AUTHORIZATION": "Bearer token"})
        assert result["AUTHORIZATION"] == "[REDACTED]"

    def test_preserves_key_name_casing(self):
        result = scrub_headers({"Authorization": "Bearer x"})
        assert "Authorization" in result

    def test_mixed_headers(self):
        headers = {
            "Content-Type": "text/csv",
            "Authorization": "Bearer abc",
            "X-Request-ID": "req-123",
        }
        result = scrub_headers(headers)
        assert result["Content-Type"] == "text/csv"
        assert result["Authorization"] == "[REDACTED]"
        assert result["X-Request-ID"] == "req-123"


# ── safe_exc_message ──────────────────────────────────────────────────────────

# A realistic JWT-shaped token for test use (not a real credential).
_FAKE_JWT = (
    "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJjbGllbnRfaWQiLCJzdWIiOiJ1c2VyQGV4YW1wbGUuY29tIn0"
    ".AAABBBCCCDDDEEEFFFGGGHHHIII"
)


class TestSafeExcMessage:
    def test_plain_message_unchanged(self):
        exc = ValueError("something went wrong")
        assert safe_exc_message(exc) == "something went wrong"

    def test_strips_jwt_from_message(self):
        exc = AuthFake(f"Token exchange failed: {_FAKE_JWT}")
        result = safe_exc_message(exc)
        assert _FAKE_JWT not in result
        assert "[REDACTED]" in result

    def test_strips_bearer_token(self):
        exc = AuthFake("Authorization: Bearer eyABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890")
        result = safe_exc_message(exc)
        assert "Bearer [REDACTED]" in result

    def test_preserves_non_sensitive_parts(self):
        exc = AuthFake("HTTP 401 from login.salesforce.com")
        result = safe_exc_message(exc)
        assert "HTTP 401" in result
        assert "login.salesforce.com" in result

    def test_multiple_jwts_all_stripped(self):
        exc = AuthFake(f"a={_FAKE_JWT} b={_FAKE_JWT}")
        result = safe_exc_message(exc)
        assert _FAKE_JWT not in result
        assert result.count("[REDACTED]") == 2


class AuthFake(Exception):
    pass


# ── safe_record_exception ─────────────────────────────────────────────────────


class TestSafeRecordException:
    def _make_span(self) -> MagicMock:
        span = MagicMock()
        span.__class__.__name__ = "Span"
        return span

    def test_sets_exception_type_attribute(self):
        span = self._make_span()
        exc = ValueError("boom")
        safe_record_exception(span, exc)
        span.set_attribute.assert_any_call("exception.type", "ValueError")

    def test_sets_sanitized_exception_message(self):
        span = self._make_span()
        exc = AuthFake(f"token={_FAKE_JWT}")
        safe_record_exception(span, exc)
        calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
        assert _FAKE_JWT not in calls["exception.message"]
        assert "[REDACTED]" in calls["exception.message"]

    def test_sets_span_status_error(self):
        from opentelemetry.trace import StatusCode

        span = self._make_span()
        exc = RuntimeError("oops")
        safe_record_exception(span, exc)
        span.set_status.assert_called_once()
        status_code_arg = span.set_status.call_args.args[0]
        assert status_code_arg == StatusCode.ERROR

    def test_noop_for_non_recording_span(self):
        from opentelemetry.trace import NonRecordingSpan, INVALID_SPAN_CONTEXT

        span = NonRecordingSpan(INVALID_SPAN_CONTEXT)
        exc = RuntimeError("oops")
        safe_record_exception(span, exc)

    def test_plain_message_passes_through_unchanged(self):
        span = self._make_span()
        exc = RuntimeError("simple error with no tokens")
        safe_record_exception(span, exc)
        calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
        assert calls["exception.message"] == "simple error with no tokens"


# ── Email-specific SCRUBBED_KEYS additions (SFBL-142) ────────────────────────


class TestEmailScrubbedKeys:
    """New keys added for the email service must be present and redacted."""

    def test_contains_email_smtp_password(self):
        assert "email_smtp_password" in SCRUBBED_KEYS

    def test_contains_ses_secret_access_key(self):
        assert "ses_secret_access_key" in SCRUBBED_KEYS

    def test_contains_aws_secret_access_key(self):
        assert "aws_secret_access_key" in SCRUBBED_KEYS

    def test_contains_to(self):
        assert "to" in SCRUBBED_KEYS

    def test_contains_to_addr(self):
        assert "to_addr" in SCRUBBED_KEYS

    def test_contains_recipient(self):
        assert "recipient" in SCRUBBED_KEYS

    def test_contains_recipients(self):
        assert "recipients" in SCRUBBED_KEYS

    def test_contains_reset_url(self):
        assert "reset_url" in SCRUBBED_KEYS

    def test_contains_confirm_url(self):
        assert "confirm_url" in SCRUBBED_KEYS

    def test_scrub_dict_redacts_reset_url(self):
        result = scrub_dict({"reset_url": "https://example.com/reset?token=abc123"})
        assert result["reset_url"] == "[REDACTED]"

    def test_scrub_dict_redacts_to_addr(self):
        result = scrub_dict({"to_addr": "user@example.com"})
        assert result["to_addr"] == "[REDACTED]"

    def test_scrub_dict_redacts_smtp_password(self):
        result = scrub_dict({"email_smtp_password": "hunter2"})
        assert result["email_smtp_password"] == "[REDACTED]"

    def test_scrub_dict_redacts_confirm_url(self):
        result = scrub_dict({"confirm_url": "https://example.com/confirm?t=xyz"})
        assert result["confirm_url"] == "[REDACTED]"

    def test_scrub_dict_redacts_recipient(self):
        result = scrub_dict({"recipient": "alice@example.com"})
        assert result["recipient"] == "[REDACTED]"

    def test_all_new_keys_are_lowercase(self):
        email_keys = {
            "email_smtp_password",
            "ses_secret_access_key",
            "aws_secret_access_key",
            "to",
            "to_addr",
            "recipient",
            "recipients",
            "reset_url",
            "confirm_url",
        }
        for key in email_keys:
            assert key == key.lower(), f"Key must be lowercase: {key!r}"
            assert key in SCRUBBED_KEYS, f"Key not found in SCRUBBED_KEYS: {key!r}"


# ── S3 URI sanitization — strip_s3_query_string (SFBL-163) ───────────────────


class TestStripS3QueryString:
    """Tests for the S3 presigned URI sanitisation rule."""

    def test_strips_query_string_from_presigned_uri(self):
        """A presigned URI has its query string removed, leaving only the base URI."""
        presigned = "s3://my-bucket/some/key?X-Amz-Signature=abc123&X-Amz-Expires=3600"
        result = strip_s3_query_string(presigned)
        assert result == "s3://my-bucket/some/key"

    def test_plain_s3_uri_passes_through_unchanged(self):
        """An S3 URI with no query string is returned as-is."""
        plain = "s3://my-bucket/some/key"
        result = strip_s3_query_string(plain)
        assert result == plain

    def test_non_s3_url_with_query_string_is_unaffected(self):
        """Non-S3 URLs with query strings are NOT modified."""
        https_url = "https://example.com/path?foo=bar&baz=qux"
        result = strip_s3_query_string(https_url)
        assert result == https_url

    def test_non_s3_plain_string_is_unaffected(self):
        """Arbitrary non-S3 strings pass through unchanged."""
        plain_string = "some-run-id-value"
        result = strip_s3_query_string(plain_string)
        assert result == plain_string

    def test_embedded_in_exception_message(self):
        """Presigned S3 URIs embedded in exception messages are sanitized by safe_exc_message."""
        presigned = "s3://my-bucket/results/run-1/step-1/partition_0.csv?X-Amz-Signature=abc123&X-Amz-Expires=3600"
        exc = RuntimeError(f"Upload failed: {presigned}")
        sanitized = safe_exc_message(exc)
        assert "X-Amz-Signature" not in sanitized
        assert "X-Amz-Expires" not in sanitized
        assert "s3://my-bucket/results/run-1/step-1/partition_0.csv" in sanitized

    def test_multiple_presigned_uris_all_stripped(self):
        """Multiple presigned S3 URIs in one string are all stripped."""
        msg = (
            "s3://bucket-a/key-a?X-Amz-Signature=aaa "
            "and s3://bucket-b/key-b?X-Amz-Credential=bbb"
        )
        result = strip_s3_query_string(msg)
        assert result == "s3://bucket-a/key-a and s3://bucket-b/key-b"
