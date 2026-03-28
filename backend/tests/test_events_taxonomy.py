"""Tests for the canonical event and outcome taxonomy in events.py.

Covers:
- All event category classes exist and expose expected constants
- All constant values are non-empty dot-separated strings
- No duplicate values within or across categories
- OutcomeCode values match the documented taxonomy
- event_name and outcome_code appear correctly in JSON log output
- The module has no runtime dependencies (stdlib-only)
"""

from __future__ import annotations

import importlib
import json
import logging

import pytest

from app.observability.events import (
    JobEvent,
    OutcomeCode,
    RunEvent,
    SalesforceEvent,
    StorageEvent,
    StepEvent,
    SystemEvent,
)
from app.observability.logging_config import _JsonFormatter


# ── Module-level structure ────────────────────────────────────────────────────


def _public_values(cls: type) -> list[str]:
    """Return all non-dunder class attribute values as strings."""
    return [
        v for k, v in vars(cls).items()
        if not k.startswith("_") and isinstance(v, str)
    ]


def test_run_event_constants_exist() -> None:
    assert RunEvent.CREATED == "run.created"
    assert RunEvent.STARTED == "run.started"
    assert RunEvent.COMPLETED == "run.completed"
    assert RunEvent.FAILED == "run.failed"
    assert RunEvent.ABORTED == "run.aborted"
    assert RunEvent.PROGRESS_UPDATED == "run.progress.updated"


def test_step_event_constants_exist() -> None:
    assert StepEvent.STARTED == "step.started"
    assert StepEvent.COMPLETED == "step.completed"
    assert StepEvent.FAILED == "step.failed"
    assert StepEvent.THRESHOLD_EXCEEDED == "step.threshold_exceeded"


def test_job_event_constants_exist() -> None:
    assert JobEvent.CREATED == "job.created"
    assert JobEvent.STATUS_CHANGED == "job.status_changed"
    assert JobEvent.COMPLETED == "job.completed"
    assert JobEvent.FAILED == "job.failed"
    assert JobEvent.ABORTED == "job.aborted"


def test_salesforce_event_constants_exist() -> None:
    assert SalesforceEvent.AUTH_REQUESTED == "salesforce.auth.requested"
    assert SalesforceEvent.AUTH_FAILED == "salesforce.auth.failed"
    assert SalesforceEvent.BULK_JOB_CREATED == "salesforce.bulk_job.created"
    assert SalesforceEvent.BULK_JOB_UPLOADED == "salesforce.bulk_job.uploaded"
    assert SalesforceEvent.BULK_JOB_CLOSED == "salesforce.bulk_job.closed"
    assert SalesforceEvent.BULK_JOB_POLLED == "salesforce.bulk_job.polled"
    assert SalesforceEvent.BULK_JOB_COMPLETED == "salesforce.bulk_job.completed"
    assert SalesforceEvent.BULK_JOB_FAILED == "salesforce.bulk_job.failed"
    assert SalesforceEvent.REQUEST_RETRIED == "salesforce.request.retried"
    assert SalesforceEvent.RATE_LIMITED == "salesforce.rate_limited"


def test_storage_event_constants_exist() -> None:
    assert StorageEvent.INPUT_LISTED == "storage.input.listed"
    assert StorageEvent.INPUT_PREVIEWED == "storage.input.previewed"
    assert StorageEvent.INPUT_FAILED == "storage.input.failed"
    assert StorageEvent.OUTPUT_PERSISTED == "storage.output.persisted"


def test_system_event_constants_exist() -> None:
    assert SystemEvent.HEALTH_CHECKED == "health.checked"
    assert SystemEvent.WEBSOCKET_CONNECTED == "websocket.connected"
    assert SystemEvent.WEBSOCKET_DISCONNECTED == "websocket.disconnected"
    assert SystemEvent.WEBSOCKET_ERROR == "websocket.error"
    assert SystemEvent.EXCEPTION_UNHANDLED == "exception.unhandled"


def test_outcome_code_baseline_constants() -> None:
    assert OutcomeCode.OK == "ok"
    assert OutcomeCode.DEGRADED == "degraded"
    assert OutcomeCode.FAILED == "failed"
    assert OutcomeCode.ABORTED == "aborted"
    assert OutcomeCode.UNEXPECTED_EXCEPTION == "unexpected_exception"


def test_outcome_code_workflow_constants() -> None:
    assert OutcomeCode.AUTH_ERROR == "auth_error"
    assert OutcomeCode.STORAGE_ERROR == "storage_error"
    assert OutcomeCode.DATABASE_ERROR == "database_error"
    assert OutcomeCode.SALESFORCE_API_ERROR == "salesforce_api_error"
    assert OutcomeCode.RATE_LIMITED == "rate_limited"
    assert OutcomeCode.NETWORK_ERROR == "network_error"
    assert OutcomeCode.TIMEOUT == "timeout"
    assert OutcomeCode.VALIDATION_ERROR == "validation_error"
    assert OutcomeCode.STEP_THRESHOLD_EXCEEDED == "step_threshold_exceeded"
    assert OutcomeCode.DEPENDENCY_UNAVAILABLE == "dependency_unavailable"
    assert OutcomeCode.CONFIGURATION_ERROR == "configuration_error"


# ── Value format constraints ──────────────────────────────────────────────────


@pytest.mark.parametrize("cls", [
    RunEvent, StepEvent, JobEvent, SalesforceEvent, StorageEvent, SystemEvent,
])
def test_event_values_are_dot_separated(cls: type) -> None:
    for value in _public_values(cls):
        assert "." in value, f"{cls.__name__}.{value!r} missing dot separator"
        assert value == value.lower(), f"{cls.__name__}.{value!r} must be lowercase"
        assert not value.startswith("."), f"{cls.__name__}.{value!r} must not start with dot"
        assert not value.endswith("."), f"{cls.__name__}.{value!r} must not end with dot"


def test_outcome_codes_are_lowercase_underscore() -> None:
    for value in _public_values(OutcomeCode):
        assert value == value.lower(), f"OutcomeCode {value!r} must be lowercase"
        assert " " not in value, f"OutcomeCode {value!r} must not contain spaces"


# ── Uniqueness ────────────────────────────────────────────────────────────────


def test_event_values_unique_within_category() -> None:
    for cls in [RunEvent, StepEvent, JobEvent, SalesforceEvent, StorageEvent, SystemEvent]:
        values = _public_values(cls)
        assert len(values) == len(set(values)), (
            f"Duplicate event names within {cls.__name__}: {values}"
        )


def test_event_values_unique_across_all_categories() -> None:
    all_values: list[str] = []
    for cls in [RunEvent, StepEvent, JobEvent, SalesforceEvent, StorageEvent, SystemEvent]:
        all_values.extend(_public_values(cls))
    assert len(all_values) == len(set(all_values)), (
        f"Duplicate event names across categories: "
        f"{[v for v in all_values if all_values.count(v) > 1]}"
    )


def test_outcome_codes_unique() -> None:
    values = _public_values(OutcomeCode)
    assert len(values) == len(set(values)), (
        f"Duplicate outcome codes: {[v for v in values if values.count(v) > 1]}"
    )


# ── JSON log integration ──────────────────────────────────────────────────────


def _make_json_record(event_name: str, outcome_code: str | None = None) -> dict:
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="test event", args=(), exc_info=None,
    )
    record.event_name = event_name  # type: ignore[attr-defined]
    if outcome_code is not None:
        record.outcome_code = outcome_code  # type: ignore[attr-defined]
    formatter = _JsonFormatter(service="test-svc", env="test")
    return json.loads(formatter.format(record))


def test_json_log_includes_event_name() -> None:
    payload = _make_json_record(RunEvent.STARTED)
    assert payload["event_name"] == "run.started"


def test_json_log_includes_outcome_code() -> None:
    payload = _make_json_record(RunEvent.COMPLETED, OutcomeCode.OK)
    assert payload["outcome_code"] == "ok"


def test_json_log_event_name_omitted_when_not_set() -> None:
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="no event", args=(), exc_info=None,
    )
    formatter = _JsonFormatter(service="test-svc", env="test")
    payload = json.loads(formatter.format(record))
    assert "event_name" not in payload


def test_json_log_all_run_events_round_trip() -> None:
    for attr in ("CREATED", "STARTED", "COMPLETED", "FAILED", "ABORTED", "PROGRESS_UPDATED"):
        value = getattr(RunEvent, attr)
        payload = _make_json_record(value)
        assert payload["event_name"] == value


def test_json_log_all_outcome_codes_round_trip() -> None:
    for attr in ("OK", "DEGRADED", "FAILED", "ABORTED", "AUTH_ERROR", "SALESFORCE_API_ERROR"):
        value = getattr(OutcomeCode, attr)
        payload = _make_json_record(RunEvent.COMPLETED, value)
        assert payload["outcome_code"] == value


# ── No runtime dependencies ───────────────────────────────────────────────────


def test_events_module_has_no_non_stdlib_imports() -> None:
    """events.py must be importable without any third-party packages."""
    import app.observability.events as events_mod
    source_file = events_mod.__file__
    assert source_file is not None
    with open(source_file) as fh:
        source = fh.read()
    # Simple heuristic: no 'import' line should reference known third-party libs
    third_party = ("sqlalchemy", "fastapi", "pydantic", "httpx", "aiosqlite")
    for lib in third_party:
        assert lib not in source, f"events.py must not import {lib}"
