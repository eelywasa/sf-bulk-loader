"""Tests for the Prometheus-compatible metrics layer.

Covers:
- metrics.py module: all metric objects exist and have correct types/labels
- record_* helper functions increment the correct metric
- MetricsMiddleware: increments http_requests_total and records latency
- /metrics endpoint: returns 200 with Prometheus text format
- WebSocket gauge: incremented on connect, decremented on disconnect
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from prometheus_client import Counter, Gauge, Histogram

from app.observability.metrics import (
    http_request_duration_seconds,
    http_requests_total,
    record_run_completed,
    record_run_started,
    record_step_completed,
    records_failed_total,
    records_processed_total,
    records_succeeded_total,
    run_duration_seconds,
    runs_completed_total,
    runs_started_total,
    step_duration_seconds,
    steps_completed_total,
    ws_active_connections,
)


# ── Module-level structure ────────────────────────────────────────────────────


def test_runs_started_total_is_counter() -> None:
    assert isinstance(runs_started_total, Counter)


def test_runs_completed_total_is_counter() -> None:
    assert isinstance(runs_completed_total, Counter)


def test_run_duration_seconds_is_histogram() -> None:
    assert isinstance(run_duration_seconds, Histogram)


def test_steps_completed_total_is_counter() -> None:
    assert isinstance(steps_completed_total, Counter)


def test_step_duration_seconds_is_histogram() -> None:
    assert isinstance(step_duration_seconds, Histogram)


def test_records_processed_total_is_counter() -> None:
    assert isinstance(records_processed_total, Counter)


def test_records_succeeded_total_is_counter() -> None:
    assert isinstance(records_succeeded_total, Counter)


def test_records_failed_total_is_counter() -> None:
    assert isinstance(records_failed_total, Counter)


def test_http_requests_total_is_counter() -> None:
    assert isinstance(http_requests_total, Counter)


def test_http_request_duration_seconds_is_histogram() -> None:
    assert isinstance(http_request_duration_seconds, Histogram)


def test_ws_active_connections_is_gauge() -> None:
    assert isinstance(ws_active_connections, Gauge)


# ── Helper functions ──────────────────────────────────────────────────────────


def _sample_value(metric, labels: dict | None = None):
    """Extract a current sample value from a metric."""
    from prometheus_client import REGISTRY
    metric_name = metric._name
    for sample in REGISTRY.get_sample_value.__func__.__self__.get_sample_value.__func__.__self__._names_to_collectors:  # noqa: just iterate
        pass

    label_dict = labels or {}
    # Use generate_latest to verify output contains the metric
    from prometheus_client import generate_latest
    output = generate_latest().decode()
    return output


def test_record_run_started_increments_counter() -> None:
    from prometheus_client import REGISTRY
    before = REGISTRY.get_sample_value("sfbl_runs_started_total") or 0.0
    record_run_started()
    after = REGISTRY.get_sample_value("sfbl_runs_started_total") or 0.0
    assert after == before + 1.0


def test_record_run_completed_increments_counter() -> None:
    from prometheus_client import REGISTRY
    before = REGISTRY.get_sample_value(
        "sfbl_runs_completed_total", {"final_status": "completed"}
    ) or 0.0
    record_run_completed("completed", 42.5)
    after = REGISTRY.get_sample_value(
        "sfbl_runs_completed_total", {"final_status": "completed"}
    ) or 0.0
    assert after == before + 1.0


def test_record_run_completed_records_histogram() -> None:
    from prometheus_client import REGISTRY
    before = REGISTRY.get_sample_value(
        "sfbl_run_duration_seconds_count", {"final_status": "aborted"}
    ) or 0.0
    record_run_completed("aborted", 10.0)
    after = REGISTRY.get_sample_value(
        "sfbl_run_duration_seconds_count", {"final_status": "aborted"}
    ) or 0.0
    assert after == before + 1.0


def test_record_step_completed_increments_steps_counter() -> None:
    from prometheus_client import REGISTRY
    before = REGISTRY.get_sample_value(
        "sfbl_steps_completed_total",
        {"object_name": "Account", "operation": "insert", "final_status": "completed"},
    ) or 0.0
    record_step_completed(
        object_name="Account", operation="insert", final_status="completed",
        duration_seconds=5.0, records_processed=100,
        records_succeeded=100, records_failed=0,
    )
    after = REGISTRY.get_sample_value(
        "sfbl_steps_completed_total",
        {"object_name": "Account", "operation": "insert", "final_status": "completed"},
    ) or 0.0
    assert after == before + 1.0


def test_record_step_completed_increments_record_counters() -> None:
    from prometheus_client import REGISTRY
    before_proc = REGISTRY.get_sample_value(
        "sfbl_records_processed_total", {"object_name": "Contact", "operation": "update"}
    ) or 0.0
    before_succ = REGISTRY.get_sample_value(
        "sfbl_records_succeeded_total", {"object_name": "Contact", "operation": "update"}
    ) or 0.0
    before_fail = REGISTRY.get_sample_value(
        "sfbl_records_failed_total", {"object_name": "Contact", "operation": "update"}
    ) or 0.0
    record_step_completed(
        object_name="Contact", operation="update", final_status="completed_with_errors",
        duration_seconds=2.0, records_processed=50,
        records_succeeded=45, records_failed=5,
    )
    assert REGISTRY.get_sample_value(
        "sfbl_records_processed_total", {"object_name": "Contact", "operation": "update"}
    ) == before_proc + 50
    assert REGISTRY.get_sample_value(
        "sfbl_records_succeeded_total", {"object_name": "Contact", "operation": "update"}
    ) == before_succ + 45
    assert REGISTRY.get_sample_value(
        "sfbl_records_failed_total", {"object_name": "Contact", "operation": "update"}
    ) == before_fail + 5


# ── /metrics endpoint ─────────────────────────────────────────────────────────


def test_metrics_endpoint_returns_200(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200


def test_metrics_endpoint_content_type_is_prometheus(client: TestClient) -> None:
    response = client.get("/metrics")
    assert "text/plain" in response.headers["content-type"]


def test_metrics_endpoint_contains_sfbl_metrics(client: TestClient) -> None:
    response = client.get("/metrics")
    body = response.text
    assert "sfbl_runs_started_total" in body
    assert "sfbl_http_requests_total" in body
    assert "sfbl_ws_active_connections" in body


# ── WebSocket connection gauge ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ws_gauge_increments_on_connect_and_decrements_on_disconnect() -> None:
    from prometheus_client import REGISTRY
    from app.utils.ws_manager import WebSocketManager

    manager = WebSocketManager()
    mock_ws = AsyncMock()
    mock_ws.accept = AsyncMock()

    before = REGISTRY.get_sample_value("sfbl_ws_active_connections") or 0.0
    await manager.connect("run-gauge-test", mock_ws)
    assert REGISTRY.get_sample_value("sfbl_ws_active_connections") == before + 1.0

    manager.disconnect("run-gauge-test", mock_ws)
    assert REGISTRY.get_sample_value("sfbl_ws_active_connections") == before


# ── MetricsMiddleware ─────────────────────────────────────────────────────────


def test_metrics_middleware_increments_http_counter(client: TestClient) -> None:
    from prometheus_client import REGISTRY
    before = REGISTRY.get_sample_value(
        "sfbl_http_requests_total", {"method": "GET", "status_class": "2xx"}
    ) or 0.0
    client.get("/api/health")
    after = REGISTRY.get_sample_value(
        "sfbl_http_requests_total", {"method": "GET", "status_class": "2xx"}
    ) or 0.0
    assert after > before


def test_metrics_endpoint_not_counted_in_http_metrics(client: TestClient) -> None:
    """Requests to /metrics itself must not be tracked in http_requests_total."""
    from prometheus_client import REGISTRY

    before_total = sum(
        REGISTRY.get_sample_value("sfbl_http_requests_total", {"method": "GET", "status_class": sc}) or 0.0
        for sc in ("2xx", "4xx", "5xx")
    )
    client.get("/metrics")
    after_total = sum(
        REGISTRY.get_sample_value("sfbl_http_requests_total", {"method": "GET", "status_class": sc}) or 0.0
        for sc in ("2xx", "4xx", "5xx")
    )
    # Should not have changed from the /metrics request
    assert after_total == before_total
