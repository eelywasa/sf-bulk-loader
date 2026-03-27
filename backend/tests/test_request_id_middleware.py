"""Tests for RequestIDMiddleware and RequestContextFilter (SFBL-40)."""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from app.observability.context import get_request_id, request_id_ctx_var
from app.observability.logging_config import configure_logging, RequestContextFilter


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_settings(**kwargs):
    """Build a Settings instance with safe test defaults."""
    import os
    from cryptography.fernet import Fernet
    os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
    from app.config import Settings
    base = {
        "encryption_key": Fernet.generate_key().decode(),
        "jwt_secret_key": "test-secret",
        "database_url": "sqlite+aiosqlite:////data/db/test.db",
    }
    base.update(kwargs)
    return Settings(**base)


# ── Request ID header behaviour ───────────────────────────────────────────────

class TestRequestIDHeader:
    def test_request_id_generated_when_header_absent(self, client):
        response = client.get("/api/health")
        assert "X-Request-ID" in response.headers
        assert response.headers["X-Request-ID"] != ""

    def test_request_id_from_header_is_adopted(self, client):
        response = client.get("/api/health", headers={"X-Request-ID": "my-upstream-id"})
        assert response.headers["X-Request-ID"] == "my-upstream-id"

    def test_request_id_is_valid_hex_when_generated(self, client):
        response = client.get("/api/health")
        rid = response.headers["X-Request-ID"]
        assert len(rid) == 32
        int(rid, 16)  # raises ValueError if not valid hex

    def test_different_requests_get_different_ids(self, client):
        r1 = client.get("/api/health")
        r2 = client.get("/api/health")
        assert r1.headers["X-Request-ID"] != r2.headers["X-Request-ID"]

    def test_request_id_present_on_all_routes(self, client):
        for path in ("/api/health", "/api/runtime"):
            response = client.get(path)
            assert "X-Request-ID" in response.headers, f"Missing on {path}"


# ── Access log ────────────────────────────────────────────────────────────────

class TestAccessLog:
    def test_access_log_emitted(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="app.observability.middleware"):
            client.get("/api/health")
        http_records = [r for r in caplog.records if r.getMessage() == "request completed"]
        assert len(http_records) >= 1

    def test_access_log_contains_event_name(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="app.observability.middleware"):
            client.get("/api/health")
        record = next(r for r in caplog.records if r.getMessage() == "request completed")
        assert getattr(record, "event_name", None) == "http.request"

    def test_access_log_contains_method(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="app.observability.middleware"):
            client.get("/api/health")
        record = next(r for r in caplog.records if r.getMessage() == "request completed")
        assert getattr(record, "method", None) == "GET"

    def test_access_log_contains_route(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="app.observability.middleware"):
            client.get("/api/health")
        record = next(r for r in caplog.records if r.getMessage() == "request completed")
        assert getattr(record, "route", None) == "/api/health"

    def test_access_log_contains_status_code(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="app.observability.middleware"):
            client.get("/api/health")
        record = next(r for r in caplog.records if r.getMessage() == "request completed")
        assert getattr(record, "status_code", None) == 200

    def test_access_log_contains_duration_ms(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="app.observability.middleware"):
            client.get("/api/health")
        record = next(r for r in caplog.records if r.getMessage() == "request completed")
        duration = getattr(record, "duration_ms", None)
        assert duration is not None
        assert isinstance(duration, float)
        assert duration >= 0

    def test_access_log_contains_request_id(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="app.observability.middleware"):
            response = client.get("/api/health", headers={"X-Request-ID": "test-id-abc"})
        record = next(r for r in caplog.records if r.getMessage() == "request completed")
        assert getattr(record, "request_id", None) == "test-id-abc"


# ── JSON log output ───────────────────────────────────────────────────────────

class TestJsonLogOutput:
    def test_request_id_in_json_log_output(self, client):
        settings = _make_settings(log_format="json")
        buffer = StringIO()
        configure_logging(settings)
        root = logging.getLogger()
        handler = root.handlers[0]
        original_stream = handler.stream
        handler.stream = buffer
        try:
            client.get("/api/health", headers={"X-Request-ID": "json-test-id"})
        finally:
            handler.stream = original_stream

        lines = [l for l in buffer.getvalue().splitlines() if l.strip()]
        http_lines = [json.loads(l) for l in lines if "http.request" in l]
        assert any(r.get("request_id") == "json-test-id" for r in http_lines)

    def test_json_access_log_has_required_fields(self, client):
        settings = _make_settings(log_format="json")
        buffer = StringIO()
        configure_logging(settings)
        root = logging.getLogger()
        handler = root.handlers[0]
        original_stream = handler.stream
        handler.stream = buffer
        try:
            client.get("/api/health")
        finally:
            handler.stream = original_stream

        lines = [l for l in buffer.getvalue().splitlines() if l.strip()]
        http_lines = [json.loads(l) for l in lines if "http.request" in l]
        assert http_lines, "No http.request JSON log entries found"
        record = http_lines[-1]
        for field in ("timestamp", "level", "message", "service", "env",
                      "request_id", "method", "route", "status_code", "duration_ms"):
            assert field in record, f"Missing field: {field}"


# ── Context variable behaviour ────────────────────────────────────────────────

class TestContextVar:
    def test_request_id_absent_outside_request(self):
        """get_request_id() returns None when called outside middleware scope."""
        assert get_request_id() is None

    def test_request_id_scoped_to_request(self, client):
        """The ContextVar is reset after the request completes."""
        client.get("/api/health")
        # After request, the ContextVar should have been reset (token.reset called)
        assert get_request_id() is None


# ── RequestContextFilter ──────────────────────────────────────────────────────

class TestRequestContextFilter:
    def test_filter_injects_request_id_from_contextvar(self):
        token = request_id_ctx_var.set("filter-test-id")
        try:
            record = logging.LogRecord(
                name="test", level=logging.INFO,
                pathname="", lineno=0, msg="hello", args=(), exc_info=None,
            )
            f = RequestContextFilter()
            f.filter(record)
            assert record.request_id == "filter-test-id"
        finally:
            request_id_ctx_var.reset(token)

    def test_filter_injects_none_when_no_request(self):
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="hello", args=(), exc_info=None,
        )
        f = RequestContextFilter()
        f.filter(record)
        assert record.request_id is None

    def test_filter_does_not_overwrite_existing_request_id(self):
        """If request_id is already set on the record (via extra={}), leave it."""
        token = request_id_ctx_var.set("ctx-id")
        try:
            record = logging.LogRecord(
                name="test", level=logging.INFO,
                pathname="", lineno=0, msg="hello", args=(), exc_info=None,
            )
            record.request_id = "explicit-id"
            f = RequestContextFilter()
            f.filter(record)
            assert record.request_id == "explicit-id"
        finally:
            request_id_ctx_var.reset(token)
