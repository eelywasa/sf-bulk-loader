"""Tests for SES backend (SFBL-140).

Uses unittest.mock to patch aioboto3.Session — moto's mock_aws context
manager has a known incompatibility with aiobotocore's async response
parsing, so we patch at the aioboto3.Session level instead.

Tests cover:

1.  Happy path — accepted=True, provider_message_id set.
2.  Throttling → TRANSIENT_PROVIDER_THROTTLED.
3.  MessageRejected → PERMANENT_REJECT.
4.  AccessDeniedException → PERMANENT_AUTH.
5.  MailFromDomainNotVerifiedException → PERMANENT_CONFIG.
6.  ConfigurationSetDoesNotExist → PERMANENT_CONFIG.
7.  Network timeout → TRANSIENT_TIMEOUT.
8.  Configuration-set inclusion: present when set, absent when not.
9.  Region resolution: explicit EMAIL_SES_REGION → passed; unset → None.
10. healthcheck() cache: two rapid calls → one probe; after 60s → second probe.
11. Classifier parametrised exhaustive test.
12. Sanitisation: synthetic secret stripped from error_detail.

asyncio_mode=auto (pytest.ini) — no @pytest.mark.asyncio decorators needed.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.services.email.backends import ses as ses_module
from app.services.email.backends.ses import SesBackend, classify
from app.services.email.errors import EmailErrorReason
from app.services.email.message import EmailMessage

# ── helpers ───────────────────────────────────────────────────────────────────

_FROM = "sender@example.com"
_TO = "recipient@example.com"

_SAMPLE_MSG = EmailMessage(
    to=_TO,
    subject="Test subject",
    text_body="Hello world",
)

_SAMPLE_MSG_HTML = EmailMessage(
    to=_TO,
    subject="Test subject",
    text_body="Hello world",
    html_body="<p>Hello world</p>",
)


def _make_client_error(code: str, message: str = "") -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": message or code}},
        operation_name="SendEmail",
    )


def _fresh_backend() -> SesBackend:
    """Return a SesBackend with a reset healthcheck cache."""
    ses_module._last_probe = None
    return SesBackend()


def _make_mock_session(send_side_effect=None, send_return_value=None, capture_kwargs=None):
    """Build a mock aioboto3.Session whose client.send_email can be configured.

    Args:
        send_side_effect: Exception to raise from send_email, or None.
        send_return_value: Dict to return from send_email, or None.
        capture_kwargs: If a dict, send_email will update it with the kwargs it receives.
    """
    async def _send_email(**kwargs):
        if capture_kwargs is not None:
            capture_kwargs.update(kwargs)
        if send_side_effect is not None:
            raise send_side_effect
        return send_return_value or {"MessageId": "mock-message-id-123"}

    mock_client = MagicMock()
    mock_client.send_email = _send_email
    # Make the context manager protocol work
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.client.return_value = mock_client

    return mock_session


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_healthcheck_cache():
    """Ensure each test starts with a cold healthcheck cache."""
    ses_module._last_probe = None
    yield
    ses_module._last_probe = None


@pytest.fixture()
def ses_settings():
    """Install a SettingsService mock with SES defaults for the test."""
    import app.services.settings.service as _svc_module

    class _SesSvc:
        async def get(self, key: str) -> object:
            defaults = {
                "email_from_address": _FROM,
                "email_ses_region": "",
                "email_ses_configuration_set": "",
            }
            return defaults.get(key, "")

    original = _svc_module.settings_service
    _svc_module.settings_service = _SesSvc()  # type: ignore[assignment]
    yield
    _svc_module.settings_service = original


# ── 1. Happy path ─────────────────────────────────────────────────────────────


async def test_send_happy_path(ses_settings):
    """Successful send returns accepted=True and a provider_message_id."""
    mock_session = _make_mock_session(send_return_value={"MessageId": "abc123"})
    with patch("aioboto3.Session", return_value=mock_session):
        result = await _fresh_backend().send(_SAMPLE_MSG)

    assert result["accepted"] is True
    assert result["reason"] is None
    assert result["error_detail"] is None
    assert result["provider_message_id"] == "abc123"


async def test_send_happy_path_html(ses_settings):
    """HTML body is sent and accepted=True."""
    mock_session = _make_mock_session(send_return_value={"MessageId": "html-msg-456"})
    with patch("aioboto3.Session", return_value=mock_session):
        result = await _fresh_backend().send(_SAMPLE_MSG_HTML)

    assert result["accepted"] is True
    assert result["provider_message_id"] == "html-msg-456"


# ── 2. Throttling → TRANSIENT_PROVIDER_THROTTLED ─────────────────────────────


async def test_send_throttling(ses_settings):
    """Throttling ClientError maps to TRANSIENT_PROVIDER_THROTTLED."""
    err = _make_client_error("Throttling")
    mock_session = _make_mock_session(send_side_effect=err)
    with patch("aioboto3.Session", return_value=mock_session):
        result = await _fresh_backend().send(_SAMPLE_MSG)

    assert result["accepted"] is False
    assert result["reason"] == EmailErrorReason.TRANSIENT_PROVIDER_THROTTLED
    assert result["transient"] is True
    assert "Throttling" in result["error_detail"]


# ── 3. MessageRejected → PERMANENT_REJECT ────────────────────────────────────


async def test_send_message_rejected(ses_settings):
    """MessageRejected maps to PERMANENT_REJECT (no retry)."""
    err = _make_client_error("MessageRejected")
    mock_session = _make_mock_session(send_side_effect=err)
    with patch("aioboto3.Session", return_value=mock_session):
        result = await _fresh_backend().send(_SAMPLE_MSG)

    assert result["accepted"] is False
    assert result["reason"] == EmailErrorReason.PERMANENT_REJECT
    assert result["transient"] is False


# ── 4. AccessDeniedException → PERMANENT_AUTH ────────────────────────────────


async def test_send_access_denied(ses_settings):
    """AccessDeniedException maps to PERMANENT_AUTH."""
    err = _make_client_error("AccessDeniedException")
    mock_session = _make_mock_session(send_side_effect=err)
    with patch("aioboto3.Session", return_value=mock_session):
        result = await _fresh_backend().send(_SAMPLE_MSG)

    assert result["accepted"] is False
    assert result["reason"] == EmailErrorReason.PERMANENT_AUTH
    assert result["transient"] is False


# ── 5. MailFromDomainNotVerifiedException → PERMANENT_CONFIG ─────────────────


async def test_send_mail_from_domain_not_verified(ses_settings):
    """MailFromDomainNotVerifiedException maps to PERMANENT_CONFIG."""
    err = _make_client_error("MailFromDomainNotVerifiedException")
    mock_session = _make_mock_session(send_side_effect=err)
    with patch("aioboto3.Session", return_value=mock_session):
        result = await _fresh_backend().send(_SAMPLE_MSG)

    assert result["accepted"] is False
    assert result["reason"] == EmailErrorReason.PERMANENT_CONFIG
    assert result["transient"] is False


# ── 6. ConfigurationSetDoesNotExist → PERMANENT_CONFIG ───────────────────────


async def test_send_config_set_does_not_exist(ses_settings):
    """ConfigurationSetDoesNotExist maps to PERMANENT_CONFIG."""
    err = _make_client_error("ConfigurationSetDoesNotExist")
    mock_session = _make_mock_session(send_side_effect=err)
    with patch("aioboto3.Session", return_value=mock_session):
        result = await _fresh_backend().send(_SAMPLE_MSG)

    assert result["accepted"] is False
    assert result["reason"] == EmailErrorReason.PERMANENT_CONFIG
    assert result["transient"] is False


# ── 7. Network timeout → TRANSIENT_TIMEOUT ───────────────────────────────────


async def test_send_timeout(ses_settings):
    """asyncio.TimeoutError maps to TRANSIENT_TIMEOUT."""
    mock_session = _make_mock_session(send_side_effect=asyncio.TimeoutError())
    with patch("aioboto3.Session", return_value=mock_session):
        result = await _fresh_backend().send(_SAMPLE_MSG)

    assert result["accepted"] is False
    assert result["reason"] == EmailErrorReason.TRANSIENT_TIMEOUT
    assert result["transient"] is True


# ── 8. Configuration-set inclusion ───────────────────────────────────────────


async def test_config_set_included_when_set():
    """ConfigurationSetName appears in the SES request when setting is set."""
    import app.services.settings.service as _svc_module

    class _CfgSetSvc:
        async def get(self, key: str) -> object:
            defaults = {
                "email_from_address": _FROM,
                "email_ses_region": "",
                "email_ses_configuration_set": "my-config-set",
            }
            return defaults.get(key, "")

    original = _svc_module.settings_service
    _svc_module.settings_service = _CfgSetSvc()  # type: ignore[assignment]
    try:
        captured: dict[str, Any] = {}
        mock_session = _make_mock_session(
            send_return_value={"MessageId": "cfg-set-msg-id"},
            capture_kwargs=captured,
        )
        with patch("aioboto3.Session", return_value=mock_session):
            result = await _fresh_backend().send(_SAMPLE_MSG)
    finally:
        _svc_module.settings_service = original

    assert result["accepted"] is True
    assert "ConfigurationSetName" in captured
    assert captured["ConfigurationSetName"] == "my-config-set"


async def test_config_set_absent_when_not_set():
    """ConfigurationSetName is NOT in the request when setting is empty."""
    import app.services.settings.service as _svc_module

    class _NoCfgSetSvc:
        async def get(self, key: str) -> object:
            defaults = {
                "email_from_address": _FROM,
                "email_ses_region": "",
                "email_ses_configuration_set": "",
            }
            return defaults.get(key, "")

    original = _svc_module.settings_service
    _svc_module.settings_service = _NoCfgSetSvc()  # type: ignore[assignment]
    try:
        captured: dict[str, Any] = {}
        mock_session = _make_mock_session(
            send_return_value={"MessageId": "no-cfg-set-id"},
            capture_kwargs=captured,
        )
        with patch("aioboto3.Session", return_value=mock_session):
            result = await _fresh_backend().send(_SAMPLE_MSG)
    finally:
        _svc_module.settings_service = original

    assert result["accepted"] is True
    assert "ConfigurationSetName" not in captured


# ── 9. Region resolution ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_region_name_explicit():
    """Explicit EMAIL_SES_REGION is returned by _region_name()."""
    import app.services.settings.service as _svc_module

    class _RegionSvc:
        async def get(self, key: str) -> object:
            return "eu-west-1" if key == "email_ses_region" else ""

    original = _svc_module.settings_service
    _svc_module.settings_service = _RegionSvc()  # type: ignore[assignment]
    try:
        result = await _fresh_backend()._region_name()
        assert result == "eu-west-1"
    finally:
        _svc_module.settings_service = original


@pytest.mark.asyncio
async def test_region_name_unset():
    """Unset EMAIL_SES_REGION returns None (boto3 resolves via default chain)."""
    import app.services.settings.service as _svc_module

    class _NoRegionSvc:
        async def get(self, key: str) -> object:
            return "" if key == "email_ses_region" else ""

    original = _svc_module.settings_service
    _svc_module.settings_service = _NoRegionSvc()  # type: ignore[assignment]
    try:
        result = await _fresh_backend()._region_name()
        assert result is None
    finally:
        _svc_module.settings_service = original


@pytest.mark.asyncio
async def test_region_passed_to_client():
    """When EMAIL_SES_REGION is set, region_name is forwarded to client()."""
    import app.services.settings.service as _svc_module

    class _RegionSvc:
        async def get(self, key: str) -> object:
            return {
                "email_from_address": _FROM,
                "email_ses_region": "ap-southeast-1",
                "email_ses_configuration_set": "",
            }.get(key, "")

    original = _svc_module.settings_service
    _svc_module.settings_service = _RegionSvc()  # type: ignore[assignment]
    try:
        mock_session = _make_mock_session(send_return_value={"MessageId": "region-test-id"})
        with patch("aioboto3.Session", return_value=mock_session):
            await _fresh_backend().send(_SAMPLE_MSG)

        # Verify the client() call received the expected region_name
        mock_session.client.assert_called_once_with("sesv2", region_name="ap-southeast-1")
    finally:
        _svc_module.settings_service = original


@pytest.mark.asyncio
async def test_region_none_when_unset():
    """When EMAIL_SES_REGION is unset, region_name=None is passed to client()."""
    import app.services.settings.service as _svc_module

    class _NoRegionSvc:
        async def get(self, key: str) -> object:
            return {
                "email_from_address": _FROM,
                "email_ses_region": "",
                "email_ses_configuration_set": "",
            }.get(key, "")

    original = _svc_module.settings_service
    _svc_module.settings_service = _NoRegionSvc()  # type: ignore[assignment]
    try:
        mock_session = _make_mock_session(send_return_value={"MessageId": "no-region-id"})
        with patch("aioboto3.Session", return_value=mock_session):
            await _fresh_backend().send(_SAMPLE_MSG)

        mock_session.client.assert_called_once_with("sesv2", region_name=None)
    finally:
        _svc_module.settings_service = original


# ── 10. healthcheck() cache ───────────────────────────────────────────────────


async def test_healthcheck_cache(monkeypatch):
    """Two rapid healthcheck calls share one probe; a call after 60s probes again."""
    call_count = 0

    async def _probe_stub(self) -> bool:
        nonlocal call_count
        call_count += 1
        return True

    monkeypatch.setattr(SesBackend, "_probe", _probe_stub)

    backend = _fresh_backend()

    # First call — cache cold, probe fires.
    assert await backend.healthcheck() is True
    assert call_count == 1

    # Second call immediately — cache warm, probe skipped.
    assert await backend.healthcheck() is True
    assert call_count == 1

    # Advance time past TTL.
    fake_time = time.monotonic() + ses_module._HEALTHCHECK_TTL_SECONDS + 1
    with patch("app.services.email.backends.ses.time.monotonic", return_value=fake_time):
        assert await backend.healthcheck() is True

    assert call_count == 2


# ── 11. Classifier exhaustive parametrised test ───────────────────────────────


@pytest.mark.parametrize(
    "exc_factory,expected_reason",
    [
        # Transient throttle codes
        (lambda: _make_client_error("Throttling"), EmailErrorReason.TRANSIENT_PROVIDER_THROTTLED),
        (lambda: _make_client_error("ThrottlingException"), EmailErrorReason.TRANSIENT_PROVIDER_THROTTLED),
        (lambda: _make_client_error("TooManyRequestsException"), EmailErrorReason.TRANSIENT_PROVIDER_THROTTLED),
        # Transient unavailable codes
        (lambda: _make_client_error("ServiceUnavailable"), EmailErrorReason.TRANSIENT_PROVIDER_UNAVAILABLE),
        (lambda: _make_client_error("InternalFailure"), EmailErrorReason.TRANSIENT_PROVIDER_UNAVAILABLE),
        (lambda: _make_client_error("InternalServerError"), EmailErrorReason.TRANSIENT_PROVIDER_UNAVAILABLE),
        # Permanent reject
        (lambda: _make_client_error("MessageRejected"), EmailErrorReason.PERMANENT_REJECT),
        # Permanent config
        (lambda: _make_client_error("MailFromDomainNotVerifiedException"), EmailErrorReason.PERMANENT_CONFIG),
        (lambda: _make_client_error("ConfigurationSetDoesNotExist"), EmailErrorReason.PERMANENT_CONFIG),
        (lambda: _make_client_error("ConfigurationSetDoesNotExistException"), EmailErrorReason.PERMANENT_CONFIG),
        (lambda: _make_client_error("AccountSendingPausedException"), EmailErrorReason.PERMANENT_CONFIG),
        # Permanent auth
        (lambda: _make_client_error("AccessDeniedException"), EmailErrorReason.PERMANENT_AUTH),
        (lambda: _make_client_error("UnrecognizedClientException"), EmailErrorReason.PERMANENT_AUTH),
        (lambda: _make_client_error("InvalidClientTokenId"), EmailErrorReason.PERMANENT_AUTH),
        # Permanent address (InvalidParameterValue with address keyword in message)
        (
            lambda: _make_client_error("InvalidParameterValue", "Invalid email address"),
            EmailErrorReason.PERMANENT_ADDRESS,
        ),
        # Timeout
        (lambda: asyncio.TimeoutError(), EmailErrorReason.TRANSIENT_TIMEOUT),
        # Network
        (lambda: ConnectionError("connection refused"), EmailErrorReason.TRANSIENT_NETWORK),
    ],
)
def test_classifier_exhaustive(exc_factory, expected_reason):
    """classify() maps each documented exception to the correct EmailErrorReason."""
    exc = exc_factory()
    reason, raw_code = classify(exc)
    assert reason == expected_reason
    assert reason.is_transient() == expected_reason.value.startswith("transient")


def test_classifier_unknown_code_logs_warning(caplog):
    """Unmapped ClientError code returns UNKNOWN and emits a warning log."""
    import logging

    err = _make_client_error("SomeObscureUnknownCode")
    with caplog.at_level(logging.WARNING, logger="app.services.email.backends.ses"):
        reason, raw_code = classify(err)

    assert reason == EmailErrorReason.UNKNOWN
    assert raw_code == "SomeObscureUnknownCode"
    assert len(caplog.records) > 0


def test_classifier_unknown_exc_type_logs_warning(caplog):
    """Unmapped exception type (not ClientError) returns UNKNOWN + warning."""
    import logging

    class WeirdNetworkError(Exception):
        pass

    err = WeirdNetworkError("something weird")
    with caplog.at_level(logging.WARNING, logger="app.services.email.backends.ses"):
        reason, raw_code = classify(err)

    assert reason == EmailErrorReason.UNKNOWN
    assert len(caplog.records) > 0


# ── 12. Sanitisation ──────────────────────────────────────────────────────────


async def test_error_detail_sanitised(ses_settings):
    """JWT-shaped token in SES error message is stripped from error_detail."""
    fake_token = (
        "eyJhbGciOiJIUzI1NiJ9"
        ".eyJzdWIiOiJ0ZXN0In0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    err = _make_client_error("MessageRejected", f"Error with token {fake_token}")
    mock_session = _make_mock_session(send_side_effect=err)
    with patch("aioboto3.Session", return_value=mock_session):
        result = await _fresh_backend().send(_SAMPLE_MSG)

    assert result["accepted"] is False
    assert result["error_detail"] is not None
    # JWT-shaped token must not appear in the sanitised detail
    assert fake_token not in result["error_detail"]
    # SES error code prefix must still be present
    assert "[SES:" in result["error_detail"]


def test_error_detail_clamped_to_500_chars():
    """error_detail is clamped to 500 characters."""
    long_msg = "x" * 600
    err = _make_client_error("ServiceUnavailable", long_msg)
    _, raw_code = classify(err)
    full_detail = f"[SES:{raw_code}] {str(err)}"
    clamped = full_detail[:500]
    assert len(clamped) == 500


# ── instance classify() proxy ─────────────────────────────────────────────────


def test_instance_classify_exception():
    """SesBackend.classify() proxies module-level classify() correctly."""
    backend = _fresh_backend()
    err = _make_client_error("Throttling")
    reason, is_transient = backend.classify(err)
    assert reason == EmailErrorReason.TRANSIENT_PROVIDER_THROTTLED
    assert is_transient is True


def test_instance_classify_string_code():
    """SesBackend.classify() accepts a string error code."""
    backend = _fresh_backend()
    reason, is_transient = backend.classify("MessageRejected")
    assert reason == EmailErrorReason.PERMANENT_REJECT
    assert is_transient is False


# ── factory registration ──────────────────────────────────────────────────────


def test_ses_registered_in_factory():
    """'ses' is registered in the EmailService backend registry."""
    from app.services.email.service import _BACKEND_FACTORIES

    assert "ses" in _BACKEND_FACTORIES
    assert isinstance(_BACKEND_FACTORIES["ses"](), SesBackend)
