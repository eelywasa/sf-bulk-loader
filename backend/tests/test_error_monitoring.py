"""Tests for optional error monitoring integration (SFBL-58)."""

import pytest
from unittest.mock import MagicMock, patch, call


class _DisabledSettings:
    error_monitoring_enabled = False
    error_monitoring_dsn = None
    service_name = "sf-bulk-loader-backend"
    app_env = "test"


class _EnabledSettings:
    error_monitoring_enabled = True
    error_monitoring_dsn = "https://fake-key@sentry.example.com/123"
    service_name = "sf-bulk-loader-backend"
    app_env = "test"


class _EnabledNoDsnSettings:
    error_monitoring_enabled = True
    error_monitoring_dsn = None
    service_name = "sf-bulk-loader-backend"
    app_env = "test"


def _reset_module():
    """Reset the error_monitoring module state between tests."""
    import app.observability.error_monitoring as em
    em._enabled = False


class TestConfigureErrorMonitoringDisabled:
    def setup_method(self):
        _reset_module()

    def teardown_method(self):
        _reset_module()

    def test_disabled_by_default(self):
        from app.observability import error_monitoring as em

        em.configure_error_monitoring(_DisabledSettings())
        assert em._enabled is False

    def test_no_sentry_init_when_disabled(self):
        with patch("sentry_sdk.init") as mock_init:
            from app.observability import error_monitoring as em
            em.configure_error_monitoring(_DisabledSettings())
            mock_init.assert_not_called()

    def test_no_sentry_init_when_dsn_missing(self):
        with patch("sentry_sdk.init") as mock_init:
            from app.observability import error_monitoring as em
            em.configure_error_monitoring(_EnabledNoDsnSettings())
            mock_init.assert_not_called()
            assert em._enabled is False


class TestConfigureErrorMonitoringEnabled:
    def setup_method(self):
        _reset_module()

    def teardown_method(self):
        _reset_module()

    def test_sentry_init_called_with_dsn(self):
        with patch("sentry_sdk.init") as mock_init:
            from app.observability import error_monitoring as em
            em.configure_error_monitoring(_EnabledSettings())
            mock_init.assert_called_once()
            kwargs = mock_init.call_args.kwargs
            assert kwargs["dsn"] == _EnabledSettings.error_monitoring_dsn

    def test_sentry_init_traces_sample_rate_zero(self):
        with patch("sentry_sdk.init") as mock_init:
            from app.observability import error_monitoring as em
            em.configure_error_monitoring(_EnabledSettings())
            kwargs = mock_init.call_args.kwargs
            assert kwargs["traces_sample_rate"] == 0.0

    def test_sentry_init_send_default_pii_false(self):
        with patch("sentry_sdk.init") as mock_init:
            from app.observability import error_monitoring as em
            em.configure_error_monitoring(_EnabledSettings())
            kwargs = mock_init.call_args.kwargs
            assert kwargs["send_default_pii"] is False

    def test_enabled_flag_set_to_true(self):
        with patch("sentry_sdk.init"):
            from app.observability import error_monitoring as em
            em.configure_error_monitoring(_EnabledSettings())
            assert em._enabled is True


class TestCaptureExceptionDisabled:
    def setup_method(self):
        _reset_module()

    def teardown_method(self):
        _reset_module()

    def test_capture_exception_noop_when_disabled(self):
        with patch("sentry_sdk.capture_exception") as mock_capture:
            from app.observability import error_monitoring as em
            em._enabled = False
            em.capture_exception(ValueError("test"), outcome_code="failed")
            mock_capture.assert_not_called()

    def test_capture_exception_does_not_raise(self):
        from app.observability import error_monitoring as em
        em._enabled = False
        # Should not raise even on unexpected errors
        em.capture_exception(RuntimeError("boom"))


class TestCaptureExceptionEnabled:
    def setup_method(self):
        _reset_module()
        import app.observability.error_monitoring as em
        em._enabled = True

    def teardown_method(self):
        _reset_module()

    def test_capture_exception_calls_sentry(self):
        with patch("sentry_sdk.capture_exception") as mock_capture, \
             patch("sentry_sdk.new_scope") as mock_scope:
            mock_scope.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_scope.return_value.__exit__ = MagicMock(return_value=False)

            from app.observability import error_monitoring as em
            exc = ValueError("test error")
            em.capture_exception(exc)
            mock_capture.assert_called_once_with(exc)

    def test_capture_exception_never_raises(self):
        """capture_exception must not propagate exceptions from Sentry itself."""
        with patch("sentry_sdk.capture_exception", side_effect=RuntimeError("sentry down")):
            with patch("sentry_sdk.new_scope") as mock_scope:
                mock_scope.return_value.__enter__ = MagicMock(return_value=MagicMock())
                mock_scope.return_value.__exit__ = MagicMock(return_value=False)

                from app.observability import error_monitoring as em
                em.capture_exception(ValueError("test"))  # must not raise


class TestScrubber:
    def test_scrubs_authorization_header(self):
        from app.observability.error_monitoring import _scrub_event

        event = {
            "request": {
                "headers": {
                    "Authorization": "Bearer super-secret-token",
                    "Content-Type": "application/json",
                }
            }
        }
        scrubbed = _scrub_event(event, {})
        assert scrubbed["request"]["headers"]["Authorization"] == "[REDACTED]"
        assert scrubbed["request"]["headers"]["Content-Type"] == "application/json"

    def test_scrubs_password_from_extra(self):
        from app.observability.error_monitoring import _scrub_event

        event = {
            "extra": {
                "password": "s3cr3t",
                "username": "admin",
            }
        }
        scrubbed = _scrub_event(event, {})
        assert scrubbed["extra"]["password"] == "[REDACTED]"
        assert scrubbed["extra"]["username"] == "admin"

    def test_scrubs_private_key(self):
        from app.observability.error_monitoring import _scrub_event

        event = {
            "request": {
                "headers": {"private_key": "BEGIN RSA..."},
            }
        }
        scrubbed = _scrub_event(event, {})
        assert scrubbed["request"]["headers"]["private_key"] == "[REDACTED]"

    def test_case_insensitive_scrubbing(self):
        from app.observability.error_monitoring import _scrub_event

        event = {
            "request": {
                "headers": {"AUTHORIZATION": "Bearer token"},
            }
        }
        scrubbed = _scrub_event(event, {})
        assert scrubbed["request"]["headers"]["AUTHORIZATION"] == "[REDACTED]"

    def test_safe_keys_preserved(self):
        from app.observability.error_monitoring import _scrub_event

        event = {
            "request": {
                "headers": {
                    "X-Request-ID": "abc-123",
                    "Content-Type": "application/json",
                }
            }
        }
        scrubbed = _scrub_event(event, {})
        assert scrubbed["request"]["headers"]["X-Request-ID"] == "abc-123"
        assert scrubbed["request"]["headers"]["Content-Type"] == "application/json"

    def test_event_with_no_request_key(self):
        from app.observability.error_monitoring import _scrub_event

        event = {"exception": {"type": "ValueError"}}
        result = _scrub_event(event, {})
        assert result == {"exception": {"type": "ValueError"}}
