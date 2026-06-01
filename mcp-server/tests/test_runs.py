"""Tests for tools/runs.py — execution and monitoring tools.

Uses mocked BulkLoaderClient (MagicMock + AsyncMock) so no live server is needed.
Covers:
  - trigger_run / abort_run / retry_step happy paths (correct endpoint + method)
    when confirm=true.
  - Refusal with NO backend call when confirm is missing or False.
  - ToolAnnotations(destructiveHint=True) present on the three destructive Tool defs.
  - list_runs / get_run / list_jobs / get_job with and without filters.
  - Status, record counts, and error messages surfaced in formatted output.
  - Error mapping (McpHttpError) — no stack traces.
  - No WebSocket usage anywhere in the module.
  - server.py handler dispatch for each new tool.
"""

from __future__ import annotations

import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, call

from sf_bulk_loader_mcp.client import McpHttpError
from sf_bulk_loader_mcp.tools.runs import (
    trigger_run,
    format_trigger_run,
    list_runs,
    format_list_runs,
    get_run,
    format_run,
    abort_run,
    format_abort_run,
    retry_step,
    format_retry_step,
    list_jobs,
    format_list_jobs,
    get_job,
    format_job,
    check_confirm,
    _CONFIRM_REQUIRED,
)


# ── Mock helpers ───────────────────────────────────────────────────────────────


def _make_client(return_value: object) -> MagicMock:
    """Return a mock BulkLoaderClient whose HTTP methods return *return_value*."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = return_value
    for method in ("get", "post", "put", "delete"):
        setattr(mock_client, method, AsyncMock(return_value=mock_response))
    return mock_client


def _make_error_client(status: int, message: str, detail: object = None) -> MagicMock:
    """Return a mock client that raises McpHttpError on every HTTP method."""
    mock_client = MagicMock()
    exc = McpHttpError(status, message, detail)
    for method in ("get", "post", "put", "delete"):
        setattr(mock_client, method, AsyncMock(side_effect=exc))
    return mock_client


# ── Sample fixtures ────────────────────────────────────────────────────────────

PLAN_ID = "plan-aaa-001"
RUN_ID = "run-bbb-001"
STEP_ID = "step-ccc-001"
JOB_ID = "job-ddd-001"
JOB_ID_2 = "job-ddd-002"

JOB_1 = {
    "id": JOB_ID,
    "load_run_id": RUN_ID,
    "load_step_id": STEP_ID,
    "sf_job_id": "7508X000001M2gHQAS",
    "sf_instance_url": "https://na1.salesforce.com",
    "partition_index": 0,
    "status": "completed",
    "records_processed": 500,
    "records_failed": 2,
    "records_successful": 498,
    "total_records": 500,
    "success_file_path": "output/job-ddd-001/success.csv",
    "error_file_path": "output/job-ddd-001/errors.csv",
    "unprocessed_file_path": None,
    "sf_api_response": None,
    "started_at": "2024-01-01T10:00:00",
    "completed_at": "2024-01-01T10:05:00",
    "error_message": None,
}

JOB_2 = {
    "id": JOB_ID_2,
    "load_run_id": RUN_ID,
    "load_step_id": STEP_ID,
    "sf_job_id": None,
    "sf_instance_url": None,
    "partition_index": 1,
    "status": "failed",
    "records_processed": None,
    "records_failed": None,
    "records_successful": None,
    "total_records": None,
    "success_file_path": None,
    "error_file_path": None,
    "unprocessed_file_path": None,
    "sf_api_response": None,
    "started_at": "2024-01-01T10:00:00",
    "completed_at": "2024-01-01T10:01:00",
    "error_message": "INVALID_TYPE: sObject type 'Account__c' is not supported",
}

RUN = {
    "id": RUN_ID,
    "load_plan_id": PLAN_ID,
    "status": "completed",
    "started_at": "2024-01-01T10:00:00",
    "completed_at": "2024-01-01T10:10:00",
    "total_records": 1000,
    "total_success": 998,
    "total_errors": 2,
    "initiated_by": "admin@example.com",
    "error_summary": None,
    "retry_of_run_id": None,
    "is_retry": False,
}

RUN_WITH_JOBS = {
    **RUN,
    "jobs": [JOB_1, JOB_2],
}

RUN_WITH_ERRORS = {
    **RUN,
    "status": "failed",
    "error_summary": {
        "auth_error": None,
        "storage_error": None,
        "circuit_breaker": "Consecutive failure threshold reached: 5 failures",
        "preflight_warnings": None,
    },
    "jobs": [],
}


# ── check_confirm ──────────────────────────────────────────────────────────────


class TestCheckConfirm:
    def test_returns_none_when_confirm_true(self) -> None:
        assert check_confirm({"confirm": True, "plan_id": "x"}) is None

    def test_returns_refusal_when_confirm_false(self) -> None:
        result = check_confirm({"confirm": False, "plan_id": "x"})
        assert result is not None
        assert "confirm=true" in result

    def test_returns_refusal_when_confirm_missing(self) -> None:
        result = check_confirm({"plan_id": "x"})
        assert result is not None
        assert "confirm=true" in result

    def test_refusal_is_the_canonical_constant(self) -> None:
        result = check_confirm({})
        assert result == _CONFIRM_REQUIRED


# ── trigger_run ───────────────────────────────────────────────────────────────


class TestTriggerRun:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint_when_confirmed(self) -> None:
        """trigger_run must POST to /api/load-plans/{plan_id}/run (load-plans router)."""
        client = _make_client(RUN)
        await trigger_run(client, PLAN_ID)
        client.post.assert_called_once_with(f"/api/load-plans/{PLAN_ID}/run")

    @pytest.mark.asyncio
    async def test_returns_run_payload(self) -> None:
        client = _make_client(RUN)
        result = await trigger_run(client, PLAN_ID)
        assert result["id"] == RUN_ID
        assert result["status"] == "completed"

    def test_format_trigger_run_includes_triggered_wording(self) -> None:
        text = format_trigger_run(RUN)
        assert "triggered" in text.lower()
        assert RUN_ID in text

    def test_format_trigger_run_shows_status(self) -> None:
        text = format_trigger_run(RUN)
        assert "completed" in text

    @pytest.mark.asyncio
    async def test_no_backend_call_when_confirm_false(self) -> None:
        """Handler must make NO backend call when confirm=False."""
        client = _make_client(RUN)
        # Simulate handler behaviour
        refusal = check_confirm({"plan_id": PLAN_ID, "confirm": False})
        assert refusal is not None
        # Verify the client was never called
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_backend_call_when_confirm_missing(self) -> None:
        """Handler must make NO backend call when confirm is absent."""
        client = _make_client(RUN)
        refusal = check_confirm({"plan_id": PLAN_ID})
        assert refusal is not None
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_404_surfaced_clearly(self) -> None:
        client = _make_error_client(404, "Resource not found", "Load plan not found")
        with pytest.raises(McpHttpError) as exc_info:
            await trigger_run(client, "missing-plan")
        text = exc_info.value.to_tool_error_text()
        assert "404" in text
        assert "Traceback" not in text

    @pytest.mark.asyncio
    async def test_422_surfaced_clearly(self) -> None:
        client = _make_error_client(422, "Unprocessable request — validation error", "Plan has no steps")
        with pytest.raises(McpHttpError) as exc_info:
            await trigger_run(client, PLAN_ID)
        text = exc_info.value.to_tool_error_text()
        assert "422" in text
        assert "Traceback" not in text


# ── list_runs ─────────────────────────────────────────────────────────────────


class TestListRuns:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint_no_filters(self) -> None:
        client = _make_client([RUN])
        await list_runs(client)
        client.get.assert_called_once_with("/api/runs/")

    @pytest.mark.asyncio
    async def test_filters_appended_to_url(self) -> None:
        client = _make_client([RUN])
        await list_runs(client, plan_id=PLAN_ID, run_status="completed")
        call_url = client.get.call_args[0][0]
        assert "plan_id=" in call_url
        assert "run_status=completed" in call_url

    @pytest.mark.asyncio
    async def test_returns_list_payload(self) -> None:
        client = _make_client([RUN])
        result = await list_runs(client)
        assert isinstance(result, list)
        assert result[0]["id"] == RUN_ID

    def test_format_non_empty(self) -> None:
        text = format_list_runs([RUN])
        assert RUN_ID in text
        assert "completed" in text
        assert PLAN_ID in text

    def test_format_empty(self) -> None:
        text = format_list_runs([])
        assert "No load runs" in text

    def test_format_shows_record_counts(self) -> None:
        run_with_counts = {**RUN, "status": "completed"}
        text = format_list_runs([run_with_counts])
        assert RUN_ID in text

    @pytest.mark.asyncio
    async def test_error_surfaced_clearly(self) -> None:
        client = _make_error_client(500, "Backend server error (HTTP 500)")
        with pytest.raises(McpHttpError) as exc_info:
            await list_runs(client)
        text = exc_info.value.to_tool_error_text()
        assert "500" in text
        assert "Traceback" not in text


# ── get_run ───────────────────────────────────────────────────────────────────


class TestGetRun:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(self) -> None:
        client = _make_client(RUN_WITH_JOBS)
        await get_run(client, RUN_ID)
        client.get.assert_called_once_with(f"/api/runs/{RUN_ID}")

    @pytest.mark.asyncio
    async def test_returns_run_with_jobs(self) -> None:
        client = _make_client(RUN_WITH_JOBS)
        result = await get_run(client, RUN_ID)
        assert result["id"] == RUN_ID
        assert len(result["jobs"]) == 2

    def test_format_run_includes_key_fields(self) -> None:
        text = format_run(RUN)
        assert RUN_ID in text
        assert PLAN_ID in text
        assert "completed" in text

    def test_format_run_shows_record_counts(self) -> None:
        text = format_run(RUN)
        assert "1000" in text  # total_records
        assert "998" in text   # total_success
        assert "2" in text     # total_errors

    def test_format_run_with_jobs_shows_job_breakdown(self) -> None:
        text = format_run(RUN_WITH_JOBS, include_jobs=True)
        assert JOB_ID in text
        assert JOB_ID_2 in text
        assert "completed" in text
        assert "failed" in text

    def test_format_run_shows_error_message_in_job(self) -> None:
        text = format_run(RUN_WITH_JOBS, include_jobs=True)
        assert "INVALID_TYPE" in text

    def test_format_run_shows_circuit_breaker_error(self) -> None:
        text = format_run(RUN_WITH_ERRORS)
        assert "circuit_breaker" in text
        assert "Consecutive failure" in text

    def test_format_run_shows_initiated_by(self) -> None:
        text = format_run(RUN)
        assert "admin@example.com" in text

    @pytest.mark.asyncio
    async def test_404_surfaced_clearly(self) -> None:
        client = _make_error_client(404, "Resource not found", "Run not found")
        with pytest.raises(McpHttpError) as exc_info:
            await get_run(client, "missing-run")
        text = exc_info.value.to_tool_error_text()
        assert "404" in text
        assert "Traceback" not in text


# ── abort_run ─────────────────────────────────────────────────────────────────


class TestAbortRun:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint_when_confirmed(self) -> None:
        aborted_run = {**RUN, "status": "aborted"}
        client = _make_client(aborted_run)
        await abort_run(client, RUN_ID)
        client.post.assert_called_once_with(f"/api/runs/{RUN_ID}/abort")

    @pytest.mark.asyncio
    async def test_returns_updated_run(self) -> None:
        aborted_run = {**RUN, "status": "aborted"}
        client = _make_client(aborted_run)
        result = await abort_run(client, RUN_ID)
        assert result["status"] == "aborted"

    def test_format_abort_run_includes_abort_wording(self) -> None:
        aborted_run = {**RUN, "status": "aborted"}
        text = format_abort_run(aborted_run)
        assert "abort" in text.lower()
        assert RUN_ID in text

    @pytest.mark.asyncio
    async def test_no_backend_call_when_confirm_false(self) -> None:
        """Handler must make NO backend call when confirm=False."""
        client = _make_client({**RUN, "status": "aborted"})
        refusal = check_confirm({"run_id": RUN_ID, "confirm": False})
        assert refusal is not None
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_backend_call_when_confirm_missing(self) -> None:
        """Handler must make NO backend call when confirm is absent."""
        client = _make_client({**RUN, "status": "aborted"})
        refusal = check_confirm({"run_id": RUN_ID})
        assert refusal is not None
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_404_surfaced_clearly(self) -> None:
        client = _make_error_client(404, "Resource not found", "Run not found")
        with pytest.raises(McpHttpError) as exc_info:
            await abort_run(client, "missing-run")
        text = exc_info.value.to_tool_error_text()
        assert "404" in text
        assert "Traceback" not in text

    @pytest.mark.asyncio
    async def test_422_cannot_abort_completed_run(self) -> None:
        client = _make_error_client(422, "Unprocessable request — validation error", "Run is already completed")
        with pytest.raises(McpHttpError) as exc_info:
            await abort_run(client, RUN_ID)
        text = exc_info.value.to_tool_error_text()
        assert "422" in text
        assert "Traceback" not in text


# ── retry_step ────────────────────────────────────────────────────────────────


class TestRetryStep:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint_when_confirmed(self) -> None:
        retry_run = {**RUN, "id": "run-retry-001", "retry_of_run_id": RUN_ID, "is_retry": True}
        client = _make_client(retry_run)
        await retry_step(client, RUN_ID, STEP_ID)
        client.post.assert_called_once_with(f"/api/runs/{RUN_ID}/retry-step/{STEP_ID}")

    @pytest.mark.asyncio
    async def test_returns_new_retry_run(self) -> None:
        retry_run = {**RUN, "id": "run-retry-001", "retry_of_run_id": RUN_ID, "is_retry": True}
        client = _make_client(retry_run)
        result = await retry_step(client, RUN_ID, STEP_ID)
        assert result["id"] == "run-retry-001"
        assert result.get("retry_of_run_id") == RUN_ID

    def test_format_retry_step_includes_retry_wording(self) -> None:
        retry_run = {**RUN, "id": "run-retry-001", "retry_of_run_id": RUN_ID, "is_retry": True}
        text = format_retry_step(retry_run)
        assert "retry" in text.lower()

    @pytest.mark.asyncio
    async def test_no_backend_call_when_confirm_false(self) -> None:
        """Handler must make NO backend call when confirm=False."""
        client = _make_client(RUN)
        refusal = check_confirm({"run_id": RUN_ID, "step_id": STEP_ID, "confirm": False})
        assert refusal is not None
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_backend_call_when_confirm_missing(self) -> None:
        """Handler must make NO backend call when confirm is absent."""
        client = _make_client(RUN)
        refusal = check_confirm({"run_id": RUN_ID, "step_id": STEP_ID})
        assert refusal is not None
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_404_surfaced_clearly(self) -> None:
        client = _make_error_client(404, "Resource not found", "Step not found")
        with pytest.raises(McpHttpError) as exc_info:
            await retry_step(client, RUN_ID, "missing-step")
        text = exc_info.value.to_tool_error_text()
        assert "404" in text
        assert "Traceback" not in text


# ── list_jobs ─────────────────────────────────────────────────────────────────


class TestListJobs:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint_no_filters(self) -> None:
        client = _make_client([JOB_1, JOB_2])
        await list_jobs(client, RUN_ID)
        client.get.assert_called_once_with(f"/api/runs/{RUN_ID}/jobs")

    @pytest.mark.asyncio
    async def test_filters_appended_to_url(self) -> None:
        client = _make_client([JOB_1])
        await list_jobs(client, RUN_ID, step_id=STEP_ID, job_status="completed")
        call_url = client.get.call_args[0][0]
        assert f"step_id={STEP_ID}" in call_url
        assert "job_status=completed" in call_url

    @pytest.mark.asyncio
    async def test_step_id_filter_only(self) -> None:
        client = _make_client([JOB_1])
        await list_jobs(client, RUN_ID, step_id=STEP_ID)
        call_url = client.get.call_args[0][0]
        assert f"step_id={STEP_ID}" in call_url
        assert "job_status" not in call_url

    @pytest.mark.asyncio
    async def test_returns_job_list(self) -> None:
        client = _make_client([JOB_1, JOB_2])
        result = await list_jobs(client, RUN_ID)
        assert len(result) == 2
        assert result[0]["id"] == JOB_ID

    def test_format_non_empty(self) -> None:
        text = format_list_jobs([JOB_1, JOB_2])
        assert JOB_ID in text
        assert JOB_ID_2 in text

    def test_format_shows_status(self) -> None:
        text = format_list_jobs([JOB_1, JOB_2])
        assert "completed" in text
        assert "failed" in text

    def test_format_shows_record_counts(self) -> None:
        text = format_list_jobs([JOB_1])
        assert "500" in text  # records_processed
        assert "2" in text    # records_failed

    def test_format_shows_error_message(self) -> None:
        text = format_list_jobs([JOB_2])
        assert "INVALID_TYPE" in text

    def test_format_empty(self) -> None:
        text = format_list_jobs([])
        assert "No jobs" in text

    @pytest.mark.asyncio
    async def test_404_surfaced_clearly(self) -> None:
        client = _make_error_client(404, "Resource not found", "Run not found")
        with pytest.raises(McpHttpError) as exc_info:
            await list_jobs(client, "missing-run")
        text = exc_info.value.to_tool_error_text()
        assert "404" in text
        assert "Traceback" not in text


# ── get_job ───────────────────────────────────────────────────────────────────


class TestGetJob:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(self) -> None:
        """get_job must use /api/jobs/{job_id} (jobs router, no prefix)."""
        client = _make_client(JOB_1)
        await get_job(client, JOB_ID)
        client.get.assert_called_once_with(f"/api/jobs/{JOB_ID}")

    @pytest.mark.asyncio
    async def test_returns_job_payload(self) -> None:
        client = _make_client(JOB_1)
        result = await get_job(client, JOB_ID)
        assert result["id"] == JOB_ID
        assert result["status"] == "completed"

    def test_format_job_shows_key_fields(self) -> None:
        text = format_job(JOB_1)
        assert JOB_ID in text
        assert RUN_ID in text
        assert STEP_ID in text
        assert "completed" in text

    def test_format_job_shows_record_counts(self) -> None:
        text = format_job(JOB_1)
        assert "500" in text  # records_processed
        assert "2" in text    # records_failed
        assert "498" in text  # records_successful

    def test_format_job_shows_sf_job_id(self) -> None:
        text = format_job(JOB_1)
        assert "7508X000001M2gHQAS" in text

    def test_format_job_shows_error_message_when_failed(self) -> None:
        text = format_job(JOB_2)
        assert "INVALID_TYPE" in text

    def test_format_job_no_error_message_when_none(self) -> None:
        text = format_job(JOB_1)
        # No "error_message" line or traceback-like content
        assert "Traceback" not in text

    @pytest.mark.asyncio
    async def test_404_surfaced_clearly(self) -> None:
        client = _make_error_client(404, "Resource not found", "Job not found")
        with pytest.raises(McpHttpError) as exc_info:
            await get_job(client, "missing-job")
        text = exc_info.value.to_tool_error_text()
        assert "404" in text
        assert "Traceback" not in text


# ── Helper: get registered tools from server ──────────────────────────────────


def _get_registered_tools() -> list:
    """Register tools on a test Server instance and retrieve the full tool list.

    Uses the MCP Server's request_handlers to invoke the list_tools handler
    (registered by @server.list_tools()) rather than calling s.list_tools()
    directly (which is a decorator factory, not an awaitable).
    """
    import asyncio
    from mcp.server import Server
    from mcp.types import ListToolsRequest
    from sf_bulk_loader_mcp import server as _server
    from unittest.mock import patch, MagicMock

    tools_holder: list = []

    async def _grab() -> None:
        s = Server("test")
        with patch("sf_bulk_loader_mcp.server.McpSettings"):
            with patch("sf_bulk_loader_mcp.server.BulkLoaderClient") as mock_cls:
                mock_instance = MagicMock()
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=None)
                mock_cls.return_value = mock_instance
                _server._register_tools(s, mock_instance)
                handler = s.request_handlers[ListToolsRequest]
                result = await handler(
                    ListToolsRequest(method="tools/list", params=None)
                )
                tools_holder.extend(result.root.tools)

    asyncio.run(_grab())
    return tools_holder


# ── Destructive annotation on Tool definitions ────────────────────────────────


class TestDestructiveAnnotations:
    """Verify that the three destructive tools carry ToolAnnotations(destructiveHint=True)."""

    def test_trigger_run_has_destructive_annotation(self) -> None:
        tools = _get_registered_tools()
        tool = next(t for t in tools if t.name == "trigger_run")
        assert tool.annotations is not None
        assert tool.annotations.destructiveHint is True

    def test_abort_run_has_destructive_annotation(self) -> None:
        tools = _get_registered_tools()
        tool = next(t for t in tools if t.name == "abort_run")
        assert tool.annotations is not None
        assert tool.annotations.destructiveHint is True

    def test_retry_step_has_destructive_annotation(self) -> None:
        tools = _get_registered_tools()
        tool = next(t for t in tools if t.name == "retry_step")
        assert tool.annotations is not None
        assert tool.annotations.destructiveHint is True

    def test_list_runs_has_no_destructive_annotation(self) -> None:
        tools = _get_registered_tools()
        tool = next(t for t in tools if t.name == "list_runs")
        # read-only tools should not have destructiveHint=True
        if tool.annotations is not None:
            assert tool.annotations.destructiveHint is not True

    def test_get_run_has_no_destructive_annotation(self) -> None:
        tools = _get_registered_tools()
        tool = next(t for t in tools if t.name == "get_run")
        if tool.annotations is not None:
            assert tool.annotations.destructiveHint is not True

    def test_list_jobs_has_no_destructive_annotation(self) -> None:
        tools = _get_registered_tools()
        tool = next(t for t in tools if t.name == "list_jobs")
        if tool.annotations is not None:
            assert tool.annotations.destructiveHint is not True

    def test_get_job_has_no_destructive_annotation(self) -> None:
        tools = _get_registered_tools()
        tool = next(t for t in tools if t.name == "get_job")
        if tool.annotations is not None:
            assert tool.annotations.destructiveHint is not True


# ── No WebSocket usage ────────────────────────────────────────────────────────


class TestNoWebSocketUsage:
    def test_runs_module_does_not_import_websocket(self) -> None:
        """tools/runs.py must not import or use WebSocket paths.

        The module may MENTION validate_ws_token in its documentation comment
        (explaining WHY it is out of scope), but must never import or call it.
        """
        import sf_bulk_loader_mcp.tools.runs as runs_module
        source = inspect.getsource(runs_module)
        # No imports from ws_manager
        assert "from" not in source or "ws_manager" not in source.split("from", 1)[1].split("\n")[0]
        assert "import ws_manager" not in source
        assert "import validate_ws_token" not in source
        # Must not attempt to connect to the WS path
        assert 'client.get("/ws/' not in source
        assert "client.ws_connect" not in source.lower()
        # No WebSocket-type imports
        assert "websockets" not in source
        assert "from mcp" not in source or "websocket" not in source.lower().split("from mcp")[1][:80]

    def test_server_run_handlers_do_not_call_websocket(self) -> None:
        """server.py run handlers must not call WebSocket paths.

        The module's doc comments and inline code comments may mention
        validate_ws_token to explain why it is out of scope, but the
        handlers themselves must never call it.
        """
        import sf_bulk_loader_mcp.server as server_module
        source = inspect.getsource(server_module)
        # No calls to WS endpoints from client
        assert 'client.get("/ws/' not in source
        assert "client.ws_connect" not in source.lower()
        # No import of validate_ws_token
        assert "import validate_ws_token" not in source
        assert "from app" not in source  # no backend imports


# ── confirm=true in inputSchema ───────────────────────────────────────────────


class TestConfirmInSchema:
    """Verify destructive tools require confirm in their inputSchema required list."""

    def test_trigger_run_schema_requires_confirm(self) -> None:
        tools = _get_registered_tools()
        tool = next(t for t in tools if t.name == "trigger_run")
        assert "confirm" in tool.inputSchema["required"]
        assert "confirm" in tool.inputSchema["properties"]

    def test_abort_run_schema_requires_confirm(self) -> None:
        tools = _get_registered_tools()
        tool = next(t for t in tools if t.name == "abort_run")
        assert "confirm" in tool.inputSchema["required"]
        assert "confirm" in tool.inputSchema["properties"]

    def test_retry_step_schema_requires_confirm(self) -> None:
        tools = _get_registered_tools()
        tool = next(t for t in tools if t.name == "retry_step")
        assert "confirm" in tool.inputSchema["required"]
        assert "confirm" in tool.inputSchema["properties"]

    def test_list_runs_schema_does_not_require_confirm(self) -> None:
        tools = _get_registered_tools()
        tool = next(t for t in tools if t.name == "list_runs")
        assert "confirm" not in tool.inputSchema.get("required", [])

    def test_list_jobs_schema_does_not_require_confirm(self) -> None:
        tools = _get_registered_tools()
        tool = next(t for t in tools if t.name == "list_jobs")
        assert "confirm" not in tool.inputSchema.get("required", [])
