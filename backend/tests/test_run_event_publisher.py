"""Tests for the typed WebSocket event publisher.

Verifies that each publish_* helper broadcasts the correct canonical
event_name, outcome_code, and domain-specific payload fields.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.observability.events import JobEvent, OutcomeCode, RunEvent, StepEvent
from app.services.run_event_publisher import (
    publish_job_status_change,
    publish_run_aborted,
    publish_run_completed,
    publish_run_failed,
    publish_run_started,
    publish_step_completed,
    publish_step_started,
)


def _patch_broadcast():
    """Return a context manager that mocks ws_manager.broadcast and captures calls."""
    return patch("app.services.run_event_publisher.ws_manager.broadcast", new_callable=AsyncMock)


# ── publish_run_started ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_started_event_name() -> None:
    with _patch_broadcast() as mock_broadcast:
        await publish_run_started("run-1")
    payload = mock_broadcast.call_args[0][1]
    assert payload["event_name"] == RunEvent.STARTED
    assert payload["run_id"] == "run-1"


# ── publish_run_failed ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_failed_event_name_and_outcome() -> None:
    with _patch_broadcast() as mock_broadcast:
        await publish_run_failed("run-1", error="auth failure")
    payload = mock_broadcast.call_args[0][1]
    assert payload["event_name"] == RunEvent.FAILED
    assert payload["outcome_code"] == OutcomeCode.FAILED
    assert payload["error"] == "auth failure"


# ── publish_run_aborted ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_aborted_event_name_and_outcome() -> None:
    with _patch_broadcast() as mock_broadcast:
        await publish_run_aborted("run-1")
    payload = mock_broadcast.call_args[0][1]
    assert payload["event_name"] == RunEvent.ABORTED
    assert payload["outcome_code"] == OutcomeCode.ABORTED
    assert "reason" not in payload


@pytest.mark.asyncio
async def test_run_aborted_includes_reason_when_provided() -> None:
    with _patch_broadcast() as mock_broadcast:
        await publish_run_aborted("run-1", reason="step_failure_threshold")
    payload = mock_broadcast.call_args[0][1]
    assert payload["event_name"] == RunEvent.ABORTED
    assert payload["reason"] == "step_failure_threshold"


# ── publish_run_completed ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_completed_ok_outcome_when_no_errors() -> None:
    with _patch_broadcast() as mock_broadcast:
        await publish_run_completed(
            "run-1", status="completed", total_records=100,
            total_success=100, total_errors=0,
        )
    payload = mock_broadcast.call_args[0][1]
    assert payload["event_name"] == RunEvent.COMPLETED
    assert payload["outcome_code"] == OutcomeCode.OK
    assert payload["total_records"] == 100
    assert payload["total_success"] == 100
    assert payload["total_errors"] == 0


@pytest.mark.asyncio
async def test_run_completed_degraded_outcome_when_errors_present() -> None:
    with _patch_broadcast() as mock_broadcast:
        await publish_run_completed(
            "run-1", status="completed_with_errors", total_records=100,
            total_success=95, total_errors=5,
        )
    payload = mock_broadcast.call_args[0][1]
    assert payload["event_name"] == RunEvent.COMPLETED
    assert payload["outcome_code"] == OutcomeCode.DEGRADED


# ── publish_step_started ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step_started_event_name() -> None:
    with _patch_broadcast() as mock_broadcast:
        await publish_step_started(
            "run-1", step_id="step-1", object_name="Account", sequence=1,
        )
    payload = mock_broadcast.call_args[0][1]
    assert payload["event_name"] == StepEvent.STARTED
    assert payload["step_id"] == "step-1"
    assert payload["object_name"] == "Account"
    assert payload["sequence"] == 1


# ── publish_step_completed ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step_completed_ok_when_not_failed() -> None:
    with _patch_broadcast() as mock_broadcast:
        await publish_step_completed(
            "run-1", step_id="step-1", object_name="Account",
            records_processed=50, records_success=50, records_failed=0,
            step_failed=False,
        )
    payload = mock_broadcast.call_args[0][1]
    assert payload["event_name"] == StepEvent.COMPLETED
    assert payload["outcome_code"] == OutcomeCode.OK
    assert payload["step_failed"] is False


@pytest.mark.asyncio
async def test_step_completed_threshold_exceeded_when_failed() -> None:
    with _patch_broadcast() as mock_broadcast:
        await publish_step_completed(
            "run-1", step_id="step-1", object_name="Account",
            records_processed=50, records_success=40, records_failed=10,
            step_failed=True,
        )
    payload = mock_broadcast.call_args[0][1]
    assert payload["event_name"] == StepEvent.THRESHOLD_EXCEEDED
    assert payload["outcome_code"] == OutcomeCode.STEP_THRESHOLD_EXCEEDED
    assert payload["step_failed"] is True


# ── publish_job_status_change ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_job_status_change_event_name() -> None:
    with _patch_broadcast() as mock_broadcast:
        await publish_job_status_change(
            "run-1", step_id="step-1", job_id="job-1", status="uploading",
        )
    payload = mock_broadcast.call_args[0][1]
    assert payload["event_name"] == JobEvent.STATUS_CHANGED
    assert payload["job_id"] == "job-1"
    assert payload["status"] == "uploading"


@pytest.mark.asyncio
async def test_job_status_change_optional_fields_omitted_when_none() -> None:
    with _patch_broadcast() as mock_broadcast:
        await publish_job_status_change(
            "run-1", step_id="step-1", job_id="job-1", status="pending",
        )
    payload = mock_broadcast.call_args[0][1]
    assert "sf_job_id" not in payload
    assert "records_processed" not in payload
    assert "records_failed" not in payload
    assert "total_records" not in payload


@pytest.mark.asyncio
async def test_job_status_change_optional_fields_included_when_provided() -> None:
    with _patch_broadcast() as mock_broadcast:
        await publish_job_status_change(
            "run-1", step_id="step-1", job_id="job-1", status="in_progress",
            sf_job_id="sf-abc", records_processed=50, records_failed=2,
            total_records=100,
        )
    payload = mock_broadcast.call_args[0][1]
    assert payload["sf_job_id"] == "sf-abc"
    assert payload["records_processed"] == 50
    assert payload["records_failed"] == 2
    assert payload["total_records"] == 100


# ── Broadcast target ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_broadcast_called_with_correct_run_id() -> None:
    """ws_manager.broadcast must be called with run_id as the first positional arg."""
    with _patch_broadcast() as mock_broadcast:
        await publish_run_started("run-xyz")
    assert mock_broadcast.call_args[0][0] == "run-xyz"


@pytest.mark.asyncio
async def test_no_legacy_event_key_in_payload() -> None:
    """WS payloads must not use the old 'event' key — only 'event_name'."""
    with _patch_broadcast() as mock_broadcast:
        await publish_run_started("run-1")
        await publish_run_failed("run-1", error="oops")
        await publish_run_completed(
            "run-1", status="completed", total_records=10,
            total_success=10, total_errors=0,
        )
    for call in mock_broadcast.call_args_list:
        payload = call[0][1]
        assert "event" not in payload, (
            f"Payload still uses legacy 'event' key: {payload}"
        )
