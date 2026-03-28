"""Tests for the centralized logging configuration module (SFBL-36)."""

from __future__ import annotations

import json
import logging
import os
from io import StringIO
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())

from app.config import Settings  # noqa: E402
from app.observability.logging_config import configure_logging  # noqa: E402

SQLITE_URL = "sqlite+aiosqlite:////data/db/test.db"


def make_settings(**kwargs) -> Settings:
    base = {
        "encryption_key": Fernet.generate_key().decode(),
        "jwt_secret_key": "test-secret",
        "database_url": SQLITE_URL,
    }
    base.update(kwargs)
    return Settings(**base)


def _capture_log_output(settings: Settings, logger_name: str = "test.logger") -> str:
    """Configure logging with the given settings and capture one log record."""
    buffer = StringIO()
    configure_logging(settings)
    root = logging.getLogger()

    # Swap the StreamHandler's stream to our buffer for inspection.
    handler = root.handlers[0]
    original_stream = handler.stream
    handler.stream = buffer
    try:
        log = logging.getLogger(logger_name)
        log.warning("test message")
    finally:
        handler.stream = original_stream

    return buffer.getvalue()


class TestPlainFormat:
    def test_plain_format_is_human_readable(self):
        settings = make_settings(log_format="plain")
        output = _capture_log_output(settings)
        assert "test message" in output
        assert "WARNING" in output

    def test_plain_format_is_not_json(self):
        settings = make_settings(log_format="plain")
        output = _capture_log_output(settings)
        with pytest.raises((json.JSONDecodeError, ValueError)):
            json.loads(output.strip())

    def test_plain_format_includes_logger_name(self):
        settings = make_settings(log_format="plain")
        output = _capture_log_output(settings, logger_name="my.module")
        assert "my.module" in output


class TestJsonFormat:
    def test_json_format_produces_valid_json(self):
        settings = make_settings(log_format="json")
        output = _capture_log_output(settings)
        record = json.loads(output.strip())
        assert isinstance(record, dict)

    def test_json_format_contains_required_fields(self):
        settings = make_settings(log_format="json")
        output = _capture_log_output(settings)
        record = json.loads(output.strip())
        for field in ("timestamp", "level", "logger", "message", "service", "env"):
            assert field in record, f"Missing required field: {field}"

    def test_json_format_service_matches_settings(self):
        settings = make_settings(log_format="json", service_name="my-custom-service")
        output = _capture_log_output(settings)
        record = json.loads(output.strip())
        assert record["service"] == "my-custom-service"

    def test_json_format_env_matches_settings(self):
        settings = make_settings(log_format="json", app_env="staging")
        output = _capture_log_output(settings)
        record = json.loads(output.strip())
        assert record["env"] == "staging"

    def test_json_format_message_is_correct(self):
        settings = make_settings(log_format="json")
        output = _capture_log_output(settings)
        record = json.loads(output.strip())
        assert record["message"] == "test message"

    def test_json_format_level_is_uppercase(self):
        settings = make_settings(log_format="json")
        output = _capture_log_output(settings)
        record = json.loads(output.strip())
        assert record["level"] == "WARNING"

    def test_json_format_timestamp_is_iso8601(self):
        settings = make_settings(log_format="json")
        output = _capture_log_output(settings)
        record = json.loads(output.strip())
        ts = record["timestamp"]
        # Must parse as ISO 8601 datetime without error.
        from datetime import datetime
        datetime.fromisoformat(ts)

    def test_json_format_one_line_per_record(self):
        settings = make_settings(log_format="json")
        output = _capture_log_output(settings)
        lines = [l for l in output.splitlines() if l.strip()]
        assert len(lines) == 1


class TestExtraFieldPassthrough:
    def _capture_with_extra(self, settings: Settings, extra: dict) -> dict:
        buffer = StringIO()
        configure_logging(settings)
        root = logging.getLogger()
        handler = root.handlers[0]
        original_stream = handler.stream
        handler.stream = buffer
        try:
            log = logging.getLogger("test.extra")
            log.warning("event occurred", extra=extra)
        finally:
            handler.stream = original_stream
        return json.loads(buffer.getvalue().strip())

    def test_event_name_passed_through(self):
        settings = make_settings(log_format="json")
        record = self._capture_with_extra(settings, {"event_name": "run.started"})
        assert record.get("event_name") == "run.started"

    def test_outcome_code_passed_through(self):
        settings = make_settings(log_format="json")
        record = self._capture_with_extra(settings, {"outcome_code": "ok"})
        assert record.get("outcome_code") == "ok"

    def test_run_id_passed_through(self):
        settings = make_settings(log_format="json")
        record = self._capture_with_extra(settings, {"run_id": 42})
        assert record.get("run_id") == 42

    def test_multiple_context_fields_passed_through(self):
        settings = make_settings(log_format="json")
        extra = {
            "run_id": 1,
            "step_id": 2,
            "job_record_id": 3,
            "sf_job_id": "SF-ABC",
        }
        record = self._capture_with_extra(settings, extra)
        assert record["run_id"] == 1
        assert record["step_id"] == 2
        assert record["job_record_id"] == 3
        assert record["sf_job_id"] == "SF-ABC"

    def test_extra_fields_absent_when_not_provided(self):
        settings = make_settings(log_format="json")
        record = self._capture_with_extra(settings, {})
        for field in ("event_name", "outcome_code", "run_id", "step_id"):
            assert field not in record


class TestLogLevel:
    def test_log_level_applied_to_root_logger(self):
        settings = make_settings(log_level="DEBUG")
        configure_logging(settings)
        assert logging.getLogger().level == logging.DEBUG

    def test_log_level_warning_applied(self):
        settings = make_settings(log_level="WARNING")
        configure_logging(settings)
        assert logging.getLogger().level == logging.WARNING

    def test_log_level_error_applied(self):
        settings = make_settings(log_level="ERROR")
        configure_logging(settings)
        assert logging.getLogger().level == logging.ERROR

    def test_log_level_info_is_default(self):
        settings = make_settings()
        configure_logging(settings)
        assert logging.getLogger().level == logging.INFO


class TestIdempotency:
    def test_repeated_calls_do_not_accumulate_handlers(self):
        settings = make_settings(log_format="plain")
        configure_logging(settings)
        configure_logging(settings)
        configure_logging(settings)
        assert len(logging.getLogger().handlers) == 1

    def test_format_switches_on_reconfigure(self):
        settings_plain = make_settings(log_format="plain")
        settings_json = make_settings(log_format="json")

        configure_logging(settings_plain)
        plain_output = _capture_log_output(settings_plain)
        with pytest.raises((json.JSONDecodeError, ValueError)):
            json.loads(plain_output.strip())

        configure_logging(settings_json)
        json_output = _capture_log_output(settings_json)
        record = json.loads(json_output.strip())
        assert "message" in record
