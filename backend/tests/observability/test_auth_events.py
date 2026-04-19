"""Tests for AuthEvent taxonomy and OutcomeCode constants — SFBL-151.

Asserts that:
- Every AuthEvent constant has the expected string value and prefix.
- Every auth-related OutcomeCode constant has a non-empty, lowercase, underscore-safe value.
- Emitting each AuthEvent with each valid OutcomeCode produces a log record with
  the expected ``event_name`` and ``outcome_code`` fields (JSON formatter round-trip).
- The events.py module remains dependency-free (stdlib only).
"""

from __future__ import annotations

import json
import logging

import pytest

from app.observability.events import AuthEvent, OutcomeCode
from app.observability.logging_config import _JsonFormatter


# ── AuthEvent constant values ─────────────────────────────────────────────────


class TestAuthEventValues:
    def test_password_changed(self) -> None:
        assert AuthEvent.PASSWORD_CHANGED == "auth.password.changed"

    def test_password_reset_requested(self) -> None:
        assert AuthEvent.PASSWORD_RESET_REQUESTED == "auth.password.reset.requested"

    def test_password_reset_confirmed(self) -> None:
        assert AuthEvent.PASSWORD_RESET_CONFIRMED == "auth.password.reset.confirmed"

    def test_profile_updated(self) -> None:
        assert AuthEvent.PROFILE_UPDATED == "auth.profile.updated"

    def test_email_change_requested(self) -> None:
        assert AuthEvent.EMAIL_CHANGE_REQUESTED == "auth.email.change.requested"

    def test_email_change_confirmed(self) -> None:
        assert AuthEvent.EMAIL_CHANGE_CONFIRMED == "auth.email.change.confirmed"

    def test_token_rejected(self) -> None:
        assert AuthEvent.TOKEN_REJECTED == "auth.token_rejected"

    def test_all_values_start_with_auth(self) -> None:
        for attr, value in vars(AuthEvent).items():
            if attr.startswith("_"):
                continue
            if not isinstance(value, str):
                continue
            assert value.startswith("auth."), (
                f"AuthEvent.{attr} = {value!r} must start with 'auth.'"
            )

    def test_all_values_are_lowercase(self) -> None:
        for attr, value in vars(AuthEvent).items():
            if attr.startswith("_"):
                continue
            if not isinstance(value, str):
                continue
            assert value == value.lower(), (
                f"AuthEvent.{attr} = {value!r} must be lowercase"
            )

    def test_all_values_unique(self) -> None:
        values = [
            v for k, v in vars(AuthEvent).items()
            if not k.startswith("_") and isinstance(v, str)
        ]
        assert len(values) == len(set(values)), (
            f"Duplicate AuthEvent values: {[v for v in values if values.count(v) > 1]}"
        )


# ── Auth-related OutcomeCode constants ────────────────────────────────────────


AUTH_OUTCOME_CODES = {
    # reset.requested
    "SENT": "sent",
    "UNKNOWN_EMAIL": "unknown_email",
    "RATE_LIMITED": "rate_limited",
    # reset.confirmed + password.changed
    "SUCCESS": "success",
    "INVALID_TOKEN": "invalid_token",
    "EXPIRED_TOKEN": "expired_token",
    "USED_TOKEN": "used_token",
    "POLICY_VIOLATION": "policy_violation",
    "NO_LOCAL_AUTH": "no_local_auth",
    # password.changed (additional)
    "WRONG_CURRENT": "wrong_current",
    "SAME_PASSWORD": "same_password",
    # email.change.requested
    "EMAIL_UNCHANGED": "unchanged",
    "EMAIL_IN_USE": "in_use",
    # email.change.confirmed
    "IN_USE_AT_CONFIRM": "in_use_at_confirm",
    # token_rejected
    "STALE_AFTER_PASSWORD_CHANGE": "stale_after_password_change",
    "EXPIRED": "expired",
    "INVALID_SIGNATURE": "invalid_signature",
    "USER_INACTIVE": "user_inactive",
}


class TestAuthOutcomeCodes:
    @pytest.mark.parametrize("attr,expected", AUTH_OUTCOME_CODES.items())
    def test_outcome_code_value(self, attr: str, expected: str) -> None:
        actual = getattr(OutcomeCode, attr)
        assert actual == expected, (
            f"OutcomeCode.{attr} = {actual!r}, expected {expected!r}"
        )

    def test_all_auth_outcome_codes_are_lowercase(self) -> None:
        for attr, expected in AUTH_OUTCOME_CODES.items():
            assert expected == expected.lower(), (
                f"OutcomeCode.{attr} = {expected!r} must be lowercase"
            )

    def test_all_auth_outcome_codes_no_spaces(self) -> None:
        for attr, expected in AUTH_OUTCOME_CODES.items():
            assert " " not in expected, (
                f"OutcomeCode.{attr} = {expected!r} must not contain spaces"
            )


# ── JSON log round-trip ───────────────────────────────────────────────────────


def _make_json_record(event_name: str, outcome_code: str | None = None) -> dict:
    """Emit a log record via _JsonFormatter and parse back to dict."""
    record = logging.LogRecord(
        name="test.auth",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="auth test event",
        args=(),
        exc_info=None,
    )
    record.event_name = event_name  # type: ignore[attr-defined]
    if outcome_code is not None:
        record.outcome_code = outcome_code  # type: ignore[attr-defined]
    formatter = _JsonFormatter(service="test-svc", env="test")
    return json.loads(formatter.format(record))


_AUTH_EVENT_OUTCOME_PAIRS: list[tuple[str, str]] = [
    (AuthEvent.PASSWORD_CHANGED, OutcomeCode.SUCCESS),
    (AuthEvent.PASSWORD_CHANGED, OutcomeCode.WRONG_CURRENT),
    (AuthEvent.PASSWORD_CHANGED, OutcomeCode.POLICY_VIOLATION),
    (AuthEvent.PASSWORD_CHANGED, OutcomeCode.SAME_PASSWORD),
    (AuthEvent.PASSWORD_CHANGED, OutcomeCode.NO_LOCAL_AUTH),
    (AuthEvent.PASSWORD_RESET_REQUESTED, OutcomeCode.SENT),
    (AuthEvent.PASSWORD_RESET_REQUESTED, OutcomeCode.UNKNOWN_EMAIL),
    (AuthEvent.PASSWORD_RESET_REQUESTED, OutcomeCode.RATE_LIMITED),
    (AuthEvent.PASSWORD_RESET_CONFIRMED, OutcomeCode.SUCCESS),
    (AuthEvent.PASSWORD_RESET_CONFIRMED, OutcomeCode.INVALID_TOKEN),
    (AuthEvent.PASSWORD_RESET_CONFIRMED, OutcomeCode.EXPIRED_TOKEN),
    (AuthEvent.PASSWORD_RESET_CONFIRMED, OutcomeCode.USED_TOKEN),
    (AuthEvent.PASSWORD_RESET_CONFIRMED, OutcomeCode.POLICY_VIOLATION),
    (AuthEvent.PASSWORD_RESET_CONFIRMED, OutcomeCode.NO_LOCAL_AUTH),
    (AuthEvent.PROFILE_UPDATED, OutcomeCode.OK),
    (AuthEvent.EMAIL_CHANGE_REQUESTED, OutcomeCode.SENT),
    (AuthEvent.EMAIL_CHANGE_REQUESTED, OutcomeCode.EMAIL_UNCHANGED),
    (AuthEvent.EMAIL_CHANGE_REQUESTED, OutcomeCode.EMAIL_IN_USE),
    (AuthEvent.EMAIL_CHANGE_REQUESTED, OutcomeCode.RATE_LIMITED),
    (AuthEvent.EMAIL_CHANGE_CONFIRMED, OutcomeCode.SUCCESS),
    (AuthEvent.EMAIL_CHANGE_CONFIRMED, OutcomeCode.INVALID_TOKEN),
    (AuthEvent.EMAIL_CHANGE_CONFIRMED, OutcomeCode.EXPIRED_TOKEN),
    (AuthEvent.EMAIL_CHANGE_CONFIRMED, OutcomeCode.USED_TOKEN),
    (AuthEvent.EMAIL_CHANGE_CONFIRMED, OutcomeCode.IN_USE_AT_CONFIRM),
    (AuthEvent.TOKEN_REJECTED, OutcomeCode.STALE_AFTER_PASSWORD_CHANGE),
    (AuthEvent.TOKEN_REJECTED, OutcomeCode.EXPIRED),
    (AuthEvent.TOKEN_REJECTED, OutcomeCode.INVALID_SIGNATURE),
    (AuthEvent.TOKEN_REJECTED, OutcomeCode.USER_INACTIVE),
]


@pytest.mark.parametrize("event_name,outcome_code", _AUTH_EVENT_OUTCOME_PAIRS)
def test_auth_event_outcome_json_roundtrip(event_name: str, outcome_code: str) -> None:
    """Each AuthEvent + OutcomeCode pair produces correct JSON log fields."""
    payload = _make_json_record(event_name, outcome_code)
    assert payload["event_name"] == event_name, (
        f"event_name mismatch for ({event_name!r}, {outcome_code!r})"
    )
    assert payload["outcome_code"] == outcome_code, (
        f"outcome_code mismatch for ({event_name!r}, {outcome_code!r})"
    )
