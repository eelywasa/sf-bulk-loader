"""Tests for tools/results.py — results and failure-inspection tools.

Uses mocked BulkLoaderClient so no live server is needed.
Covers:
  - preview_success/error/unprocessed_csv: correct endpoint + query params
  - Pagination (offset/limit) propagated to backend URL
  - Column filtering (client-side projection)
  - Default row limit (50) applied when limit is None
  - Hard-max row-limit clamping (>500 → 500)
  - Total-cell-bytes cap + truncation marker
  - download_logs_zip: saves file to tmp dir; returns path + member listing
  - failure_summary: aggregates error reasons; 404 jobs skipped gracefully
  - files.view_contents endpoints work in desktop mode (no auth header needed)
    — verified by checking no Authorization header is injected in auth_mode=none
  - Error mapping (McpHttpError → no stack traces)
  - Redaction nuance: row data appears in tool result; NOT in log output
  - server.py handler dispatch for each new tool
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sf_bulk_loader_mcp.client import McpHttpError
from sf_bulk_loader_mcp.tools.results import (
    DEFAULT_ROW_LIMIT,
    MAX_ROW_LIMIT,
    MAX_CELL_BYTES,
    _TRUNCATED_MARKER,
    _clamp_limit,
    _apply_cell_byte_cap,
    _project_columns,
    preview_success_csv,
    preview_error_csv,
    preview_unprocessed_csv,
    download_logs_zip,
    failure_summary,
    format_preview,
    format_download_logs_zip,
    format_failure_summary,
)


# ── Mock helpers ───────────────────────────────────────────────────────────────


def _make_client(return_value: object) -> MagicMock:
    """Return a mock BulkLoaderClient whose GET method returns *return_value*."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = return_value
    mock_response.content = b""  # default empty bytes
    for method in ("get", "post", "put", "delete"):
        setattr(mock_client, method, AsyncMock(return_value=mock_response))
    return mock_client


def _make_bytes_client(content: bytes) -> MagicMock:
    """Return a mock client whose GET returns a response with .content = *content*."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = content
    mock_response.json.return_value = {}
    mock_client.get = AsyncMock(return_value=mock_response)
    return mock_client


def _make_error_client(status: int, message: str, detail: object = None) -> MagicMock:
    """Return a mock client that raises McpHttpError on every HTTP method."""
    mock_client = MagicMock()
    exc = McpHttpError(status, message, detail)
    for method in ("get", "post", "put", "delete"):
        setattr(mock_client, method, AsyncMock(side_effect=exc))
    return mock_client


# ── Sample data ────────────────────────────────────────────────────────────────

JOB_ID = "job-001"
RUN_ID = "run-001"

# A typical backend preview response
PREVIEW_PAYLOAD = {
    "filename": "success.csv",
    "header": ["Id", "Name", "sf__Created"],
    "rows": [
        {"Id": "0011000001", "Name": "Acme Corp", "sf__Created": "true"},
        {"Id": "0011000002", "Name": "Beta Ltd", "sf__Created": "false"},
    ],
    "total_rows": None,
    "filtered_rows": None,
    "offset": 0,
    "limit": 50,
    "has_next": False,
}

ERROR_PREVIEW_PAYLOAD = {
    "filename": "errors.csv",
    "header": ["Id", "Name", "sf__Error"],
    "rows": [
        {"Id": "0011000003", "Name": "Bad Corp", "sf__Error": "FIELD_INTEGRITY_EXCEPTION: Phone must be numeric"},
        {"Id": "0011000004", "Name": "Bad Corp 2", "sf__Error": "FIELD_INTEGRITY_EXCEPTION: Phone must be numeric"},
        {"Id": "0011000005", "Name": "Invalid Corp", "sf__Error": "REQUIRED_FIELD_MISSING: Name is required"},
    ],
    "total_rows": 3,
    "filtered_rows": 3,
    "offset": 0,
    "limit": 50,
    "has_next": False,
}


# ── _clamp_limit ───────────────────────────────────────────────────────────────


class TestClampLimit:
    def test_none_returns_default(self) -> None:
        assert _clamp_limit(None) == DEFAULT_ROW_LIMIT

    def test_value_within_range_unchanged(self) -> None:
        assert _clamp_limit(10) == 10
        assert _clamp_limit(1) == 1
        assert _clamp_limit(500) == 500

    def test_above_max_clamped_to_max(self) -> None:
        assert _clamp_limit(501) == MAX_ROW_LIMIT
        assert _clamp_limit(10000) == MAX_ROW_LIMIT

    def test_zero_clamped_to_one(self) -> None:
        assert _clamp_limit(0) == 1

    def test_negative_clamped_to_one(self) -> None:
        assert _clamp_limit(-5) == 1

    def test_exactly_max_passes(self) -> None:
        assert _clamp_limit(MAX_ROW_LIMIT) == MAX_ROW_LIMIT


# ── _apply_cell_byte_cap ───────────────────────────────────────────────────────


class TestApplyCellByteCap:
    def test_empty_rows_returns_empty(self) -> None:
        assert _apply_cell_byte_cap([]) == []

    def test_small_rows_unchanged(self) -> None:
        rows = [{"Id": "001", "Name": "Acme"}]
        result = _apply_cell_byte_cap(rows)
        assert result == rows
        assert _TRUNCATED_MARKER not in result

    def test_truncation_appends_marker(self) -> None:
        # Create rows whose total cell bytes exceed the cap
        big_value = "x" * (MAX_CELL_BYTES // 2 + 1)
        rows = [
            {"col": big_value},
            {"col": big_value},
            {"col": big_value},
        ]
        result = _apply_cell_byte_cap(rows)
        # Should have fewer rows than input and end with the marker
        assert len(result) < len(rows)
        assert result[-1] == _TRUNCATED_MARKER

    def test_truncation_marker_is_dict(self) -> None:
        assert isinstance(_TRUNCATED_MARKER, dict)
        assert "__truncated__" in _TRUNCATED_MARKER

    def test_all_rows_fit_no_truncation(self) -> None:
        rows = [{"Id": str(i), "Value": "small"} for i in range(10)]
        result = _apply_cell_byte_cap(rows)
        assert result == rows


# ── _project_columns ──────────────────────────────────────────────────────────


class TestProjectColumns:
    def test_no_columns_returns_payload_unchanged(self) -> None:
        result = _project_columns(PREVIEW_PAYLOAD, None)
        assert result["rows"] == PREVIEW_PAYLOAD["rows"]

    def test_columns_filter_narrows_rows(self) -> None:
        result = _project_columns(PREVIEW_PAYLOAD, ["Id", "Name"])
        for row in result["rows"]:
            assert "sf__Created" not in row
            assert "Id" in row
            assert "Name" in row

    def test_metadata_preserved_after_projection(self) -> None:
        result = _project_columns(PREVIEW_PAYLOAD, ["Id"])
        assert result["header"] == PREVIEW_PAYLOAD["header"]
        assert result["offset"] == PREVIEW_PAYLOAD["offset"]
        assert result["limit"] == PREVIEW_PAYLOAD["limit"]
        assert result["has_next"] == PREVIEW_PAYLOAD["has_next"]


# ── preview_success_csv ────────────────────────────────────────────────────────


class TestPreviewSuccessCsv:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(self) -> None:
        client = _make_client(PREVIEW_PAYLOAD)
        await preview_success_csv(client, JOB_ID)
        url = client.get.call_args[0][0]
        assert url.startswith(f"/api/jobs/{JOB_ID}/success-csv/preview")

    @pytest.mark.asyncio
    async def test_default_limit_is_50(self) -> None:
        client = _make_client(PREVIEW_PAYLOAD)
        await preview_success_csv(client, JOB_ID)
        url = client.get.call_args[0][0]
        assert f"limit={DEFAULT_ROW_LIMIT}" in url

    @pytest.mark.asyncio
    async def test_explicit_limit_passed(self) -> None:
        client = _make_client(PREVIEW_PAYLOAD)
        await preview_success_csv(client, JOB_ID, limit=10)
        url = client.get.call_args[0][0]
        assert "limit=10" in url

    @pytest.mark.asyncio
    async def test_limit_clamped_to_max(self) -> None:
        client = _make_client(PREVIEW_PAYLOAD)
        await preview_success_csv(client, JOB_ID, limit=9999)
        url = client.get.call_args[0][0]
        assert f"limit={MAX_ROW_LIMIT}" in url

    @pytest.mark.asyncio
    async def test_offset_passed(self) -> None:
        client = _make_client(PREVIEW_PAYLOAD)
        await preview_success_csv(client, JOB_ID, offset=100)
        url = client.get.call_args[0][0]
        assert "offset=100" in url

    @pytest.mark.asyncio
    async def test_filters_serialised_as_json(self) -> None:
        client = _make_client(PREVIEW_PAYLOAD)
        filters = [{"column": "Name", "value": "Acme"}]
        await preview_success_csv(client, JOB_ID, filters=filters)
        url = client.get.call_args[0][0]
        assert "filters=" in url
        assert "Name" in url

    @pytest.mark.asyncio
    async def test_column_projection_applied(self) -> None:
        client = _make_client(PREVIEW_PAYLOAD)
        result = await preview_success_csv(client, JOB_ID, columns=["Id"])
        for row in result["rows"]:
            if "__truncated__" not in row:
                assert "sf__Created" not in row
                assert "Name" not in row
                assert "Id" in row

    @pytest.mark.asyncio
    async def test_returns_payload_with_rows(self) -> None:
        client = _make_client(PREVIEW_PAYLOAD)
        result = await preview_success_csv(client, JOB_ID)
        assert "rows" in result
        assert "header" in result

    @pytest.mark.asyncio
    async def test_404_surfaced_without_traceback(self) -> None:
        client = _make_error_client(404, "Resource not found", "Job not found")
        with pytest.raises(McpHttpError) as exc_info:
            await preview_success_csv(client, "missing-job")
        text = exc_info.value.to_tool_error_text()
        assert "404" in text
        assert "Traceback" not in text

    @pytest.mark.asyncio
    async def test_403_surfaced_without_traceback(self) -> None:
        """files.view_contents permission denial is surfaced as 403."""
        client = _make_error_client(403, "Forbidden — insufficient permissions")
        with pytest.raises(McpHttpError) as exc_info:
            await preview_success_csv(client, JOB_ID)
        text = exc_info.value.to_tool_error_text()
        assert "403" in text
        assert "Traceback" not in text


# ── preview_error_csv ─────────────────────────────────────────────────────────


class TestPreviewErrorCsv:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(self) -> None:
        client = _make_client(ERROR_PREVIEW_PAYLOAD)
        await preview_error_csv(client, JOB_ID)
        url = client.get.call_args[0][0]
        assert f"/api/jobs/{JOB_ID}/error-csv/preview" in url

    @pytest.mark.asyncio
    async def test_returns_error_rows(self) -> None:
        client = _make_client(ERROR_PREVIEW_PAYLOAD)
        result = await preview_error_csv(client, JOB_ID)
        assert len(result["rows"]) == 3
        assert result["rows"][0]["sf__Error"].startswith("FIELD_INTEGRITY_EXCEPTION")

    @pytest.mark.asyncio
    async def test_default_limit_50(self) -> None:
        client = _make_client(ERROR_PREVIEW_PAYLOAD)
        await preview_error_csv(client, JOB_ID)
        url = client.get.call_args[0][0]
        assert f"limit={DEFAULT_ROW_LIMIT}" in url


# ── preview_unprocessed_csv ───────────────────────────────────────────────────


class TestPreviewUnprocessedCsv:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(self) -> None:
        client = _make_client(PREVIEW_PAYLOAD)
        await preview_unprocessed_csv(client, JOB_ID)
        url = client.get.call_args[0][0]
        assert f"/api/jobs/{JOB_ID}/unprocessed-csv/preview" in url

    @pytest.mark.asyncio
    async def test_default_limit_50(self) -> None:
        client = _make_client(PREVIEW_PAYLOAD)
        await preview_unprocessed_csv(client, JOB_ID)
        url = client.get.call_args[0][0]
        assert f"limit={DEFAULT_ROW_LIMIT}" in url


# ── Cell-byte cap integration (via preview helpers) ───────────────────────────


class TestCellByteCapIntegration:
    @pytest.mark.asyncio
    async def test_large_rows_are_truncated(self) -> None:
        """When backend returns rows whose total bytes exceed the cap, truncation occurs."""
        big_value = "x" * (MAX_CELL_BYTES // 2 + 1)
        payload_with_large_rows = {
            **PREVIEW_PAYLOAD,
            "rows": [
                {"Id": "001", "val": big_value},
                {"Id": "002", "val": big_value},
                {"Id": "003", "val": big_value},
            ],
        }
        client = _make_client(payload_with_large_rows)
        result = await preview_success_csv(client, JOB_ID)
        assert result["rows"][-1] == _TRUNCATED_MARKER

    @pytest.mark.asyncio
    async def test_small_rows_not_truncated(self) -> None:
        client = _make_client(PREVIEW_PAYLOAD)
        result = await preview_success_csv(client, JOB_ID)
        assert _TRUNCATED_MARKER not in result["rows"]


# ── download_logs_zip ─────────────────────────────────────────────────────────


def _make_zip_bytes(members: list[tuple[str, bytes]]) -> bytes:
    """Build a valid in-memory ZIP with the given (filename, content) pairs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


class TestDownloadLogsZip:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(self, tmp_path: Path) -> None:
        zip_bytes = _make_zip_bytes([("step1/partition_0_errors.csv", b"Id,sf__Error\n")])
        client = _make_bytes_client(zip_bytes)
        await download_logs_zip(client, RUN_ID, data_dir_override=tmp_path)
        url = client.get.call_args[0][0]
        assert f"/api/runs/{RUN_ID}/logs.zip" in url

    @pytest.mark.asyncio
    async def test_saves_file_to_data_dir(self, tmp_path: Path) -> None:
        zip_bytes = _make_zip_bytes([("step1/partition_0_success.csv", b"Id\n001\n")])
        client = _make_bytes_client(zip_bytes)
        result = await download_logs_zip(client, RUN_ID, data_dir_override=tmp_path)
        saved = Path(result["saved_path"])
        assert saved.exists()
        assert saved.suffix == ".zip"

    @pytest.mark.asyncio
    async def test_returns_member_listing(self, tmp_path: Path) -> None:
        zip_bytes = _make_zip_bytes([
            ("step1/partition_0_success.csv", b"Id\n001\n002\n"),
            ("step1/partition_0_errors.csv", b"Id,sf__Error\n003,FIELD_ERROR\n"),
        ])
        client = _make_bytes_client(zip_bytes)
        result = await download_logs_zip(client, RUN_ID, data_dir_override=tmp_path)
        assert result["member_count"] == 2
        names = [m["name"] for m in result["members"]]
        assert "step1/partition_0_success.csv" in names
        assert "step1/partition_0_errors.csv" in names

    @pytest.mark.asyncio
    async def test_members_include_size_bytes(self, tmp_path: Path) -> None:
        content = b"Id\n" + b"x" * 100
        zip_bytes = _make_zip_bytes([("step1/result.csv", content)])
        client = _make_bytes_client(zip_bytes)
        result = await download_logs_zip(client, RUN_ID, data_dir_override=tmp_path)
        assert result["members"][0]["size_bytes"] > 0

    @pytest.mark.asyncio
    async def test_saved_path_contains_run_id_prefix(self, tmp_path: Path) -> None:
        zip_bytes = _make_zip_bytes([])
        client = _make_bytes_client(zip_bytes)
        result = await download_logs_zip(client, RUN_ID, data_dir_override=tmp_path)
        assert RUN_ID[:8] in result["saved_path"]

    @pytest.mark.asyncio
    async def test_query_params_for_selective_download(self, tmp_path: Path) -> None:
        """success=False should be reflected in the query string."""
        zip_bytes = _make_zip_bytes([])
        client = _make_bytes_client(zip_bytes)
        await download_logs_zip(client, RUN_ID, success=False, data_dir_override=tmp_path)
        url = client.get.call_args[0][0]
        assert "success=false" in url

    @pytest.mark.asyncio
    async def test_empty_query_when_all_true(self, tmp_path: Path) -> None:
        """When success/errors/unprocessed are all True (default), no query params added."""
        zip_bytes = _make_zip_bytes([])
        client = _make_bytes_client(zip_bytes)
        await download_logs_zip(client, RUN_ID, data_dir_override=tmp_path)
        url = client.get.call_args[0][0]
        # No query string appended for default (all-true) case
        assert "?" not in url

    @pytest.mark.asyncio
    async def test_repeated_calls_overwrite_file(self, tmp_path: Path) -> None:
        """Two calls for the same run_id write to the same path."""
        zip_bytes = _make_zip_bytes([("result.csv", b"Id\n001\n")])
        client = _make_bytes_client(zip_bytes)
        result1 = await download_logs_zip(client, RUN_ID, data_dir_override=tmp_path)
        result2 = await download_logs_zip(client, RUN_ID, data_dir_override=tmp_path)
        assert result1["saved_path"] == result2["saved_path"]

    @pytest.mark.asyncio
    async def test_404_surfaced_without_traceback(self, tmp_path: Path) -> None:
        client = _make_error_client(404, "Resource not found", "Run not found")
        with pytest.raises(McpHttpError) as exc_info:
            await download_logs_zip(client, "missing-run", data_dir_override=tmp_path)
        text = exc_info.value.to_tool_error_text()
        assert "404" in text
        assert "Traceback" not in text


# ── failure_summary ────────────────────────────────────────────────────────────


class TestFailureSummary:
    @pytest.mark.asyncio
    async def test_aggregates_errors_by_message(self) -> None:
        """failure_summary groups sf__Error values by frequency."""
        client = _make_client(ERROR_PREVIEW_PAYLOAD)
        result = await failure_summary(client, [JOB_ID])
        top_errors = {e["error"]: e["count"] for e in result["top_errors"]}
        assert top_errors.get("FIELD_INTEGRITY_EXCEPTION: Phone must be numeric") == 2
        assert top_errors.get("REQUIRED_FIELD_MISSING: Name is required") == 1

    @pytest.mark.asyncio
    async def test_total_error_rows_correct(self) -> None:
        client = _make_client(ERROR_PREVIEW_PAYLOAD)
        result = await failure_summary(client, [JOB_ID])
        assert result["total_error_rows"] == 3

    @pytest.mark.asyncio
    async def test_jobs_with_errors_counted(self) -> None:
        client = _make_client(ERROR_PREVIEW_PAYLOAD)
        result = await failure_summary(client, [JOB_ID])
        assert result["jobs_with_errors"] == 1

    @pytest.mark.asyncio
    async def test_404_job_skipped_gracefully(self) -> None:
        """A job whose error CSV returns 404 is added to jobs_unavailable."""
        client = MagicMock()
        ok_response = MagicMock()
        ok_response.json.return_value = ERROR_PREVIEW_PAYLOAD
        not_found_exc = McpHttpError(404, "Resource not found", "Job not found")

        call_count = 0

        async def _side_effect(url: str, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if "job-ok" in url:
                return ok_response
            raise not_found_exc

        client.get = AsyncMock(side_effect=_side_effect)
        result = await failure_summary(client, ["job-ok", "job-missing"])
        assert "job-missing" in result["jobs_unavailable"]
        assert result["jobs_with_errors"] == 1

    @pytest.mark.asyncio
    async def test_multiple_jobs_aggregated(self) -> None:
        """Errors from multiple jobs are aggregated into a single Counter."""
        payload1 = {
            **ERROR_PREVIEW_PAYLOAD,
            "rows": [{"Id": "001", "sf__Error": "ERR_A"}],
        }
        payload2 = {
            **ERROR_PREVIEW_PAYLOAD,
            "rows": [{"Id": "002", "sf__Error": "ERR_A"}, {"Id": "003", "sf__Error": "ERR_B"}],
        }
        responses = [MagicMock(), MagicMock()]
        responses[0].json.return_value = payload1
        responses[1].json.return_value = payload2
        idx = iter(responses)
        client = MagicMock()
        client.get = AsyncMock(side_effect=lambda *a, **kw: next(idx))

        result = await failure_summary(client, ["job-001", "job-002"])
        top_errors = {e["error"]: e["count"] for e in result["top_errors"]}
        assert top_errors["ERR_A"] == 2
        assert top_errors["ERR_B"] == 1
        assert result["total_error_rows"] == 3
        assert result["jobs_with_errors"] == 2

    @pytest.mark.asyncio
    async def test_empty_job_list(self) -> None:
        client = _make_client({})
        result = await failure_summary(client, [])
        assert result["total_error_rows"] == 0
        assert result["jobs_with_errors"] == 0
        assert result["top_errors"] == []

    @pytest.mark.asyncio
    async def test_top_n_respected(self) -> None:
        """top_n limits number of error groups returned."""
        rows = [{"Id": str(i), "sf__Error": f"ERR_{i}"} for i in range(30)]
        payload = {**ERROR_PREVIEW_PAYLOAD, "rows": rows}
        client = _make_client(payload)
        result = await failure_summary(client, [JOB_ID], top_n=5)
        assert len(result["top_errors"]) <= 5

    @pytest.mark.asyncio
    async def test_non_404_error_propagated(self) -> None:
        """A 500 error from the backend should propagate, not be silently swallowed."""
        client = _make_error_client(500, "Backend server error (HTTP 500)")
        with pytest.raises(McpHttpError) as exc_info:
            await failure_summary(client, [JOB_ID])
        assert "500" in exc_info.value.to_tool_error_text()


# ── desktop_no_auth: files.view_contents in auth_mode=none ───────────────────


class TestDesktopNoAuth:
    """Verify no Authorization header is injected for preview/download calls in
    desktop (auth_mode=none) mode.

    We test this by inspecting the build_headers output for a desktop-configured
    client, which must NOT include 'Authorization'.
    """

    def test_desktop_client_sends_no_auth_header(self) -> None:
        from sf_bulk_loader_mcp.config import McpSettings, AuthMode

        settings = McpSettings(
            auth_mode=AuthMode.NONE,
            bulkloader_base_url="http://localhost:8000",
        )
        from sf_bulk_loader_mcp.client import BulkLoaderClient
        client = BulkLoaderClient(settings)
        headers = client._build_headers()
        assert "Authorization" not in headers


# ── Redaction nuance ──────────────────────────────────────────────────────────


class TestRedactionNuance:
    """Row data must appear in the tool result but NOT in log output.

    The test verifies:
      1. The tool result contains the row data (it's the purpose of the tool).
      2. The logger.debug / logger.info calls in results.py do NOT include row
         content in their `extra` dicts.
    """

    @pytest.mark.asyncio
    async def test_row_data_in_result_not_in_logs(self, caplog) -> None:
        """Row values appear in the returned payload but not in captured log records."""
        sentinel = "SENTINEL_VALUE_XY99ZZ"
        payload = {
            **PREVIEW_PAYLOAD,
            "rows": [{"Id": "001", "Name": sentinel}],
        }
        client = _make_client(payload)

        with caplog.at_level(logging.DEBUG, logger="sf_bulk_loader_mcp.tools.results"):
            result = await preview_success_csv(client, JOB_ID)

        # 1. Row data IS in the result.
        assert any(
            sentinel in str(row.get("Name", ""))
            for row in result["rows"]
            if "__truncated__" not in row
        )

        # 2. Row data is NOT in any log record emitted by results.py.
        for record in caplog.records:
            # Check both message and extra dict
            assert sentinel not in record.getMessage()
            extra_str = json.dumps(record.__dict__.get("extra") or {})
            assert sentinel not in extra_str

    @pytest.mark.asyncio
    async def test_failure_summary_logs_only_counts(self, caplog) -> None:
        """failure_summary logs aggregate counts, never individual row content."""
        sentinel = "SENSITIVE_ERROR_MSG_12345"
        payload = {
            **ERROR_PREVIEW_PAYLOAD,
            "rows": [{"Id": "001", "sf__Error": sentinel}],
        }
        client = _make_client(payload)

        with caplog.at_level(logging.DEBUG, logger="sf_bulk_loader_mcp.tools.results"):
            await failure_summary(client, [JOB_ID])

        for record in caplog.records:
            assert sentinel not in record.getMessage()
            extra_str = json.dumps(record.__dict__.get("extra") or {})
            assert sentinel not in extra_str


# ── Formatters ────────────────────────────────────────────────────────────────


class TestFormatPreview:
    def test_format_includes_job_id(self) -> None:
        text = format_preview(PREVIEW_PAYLOAD, "success", JOB_ID)
        assert JOB_ID in text

    def test_format_includes_result_type(self) -> None:
        text = format_preview(PREVIEW_PAYLOAD, "success", JOB_ID)
        assert "Success" in text

    def test_format_includes_column_names(self) -> None:
        text = format_preview(PREVIEW_PAYLOAD, "success", JOB_ID)
        assert "Id" in text
        assert "Name" in text

    def test_format_shows_row_count(self) -> None:
        text = format_preview(PREVIEW_PAYLOAD, "success", JOB_ID)
        assert "rows returned: 2" in text

    def test_format_indicates_truncation(self) -> None:
        truncated_payload = {
            **PREVIEW_PAYLOAD,
            "rows": [{"Id": "001", "Name": "X"}, _TRUNCATED_MARKER],
        }
        text = format_preview(truncated_payload, "success", JOB_ID)
        assert "truncated" in text.lower() or "cell byte cap" in text.lower()

    def test_format_no_rows(self) -> None:
        empty_payload = {**PREVIEW_PAYLOAD, "rows": [], "header": []}
        text = format_preview(empty_payload, "success", JOB_ID)
        assert "no rows" in text.lower()


class TestFormatDownloadLogsZip:
    def test_includes_saved_path(self) -> None:
        payload = {
            "saved_path": "/tmp/run-logs/run_abc12345.zip",
            "members": [{"name": "step1/part0_errors.csv", "size_bytes": 1024}],
            "member_count": 1,
        }
        text = format_download_logs_zip(payload, RUN_ID)
        assert "/tmp/run-logs/run_abc12345.zip" in text

    def test_includes_member_names(self) -> None:
        payload = {
            "saved_path": "/tmp/x.zip",
            "members": [{"name": "step1/part0_errors.csv", "size_bytes": 500}],
            "member_count": 1,
        }
        text = format_download_logs_zip(payload, RUN_ID)
        assert "step1/part0_errors.csv" in text

    def test_empty_archive_handled(self) -> None:
        payload = {"saved_path": "/tmp/x.zip", "members": [], "member_count": 0}
        text = format_download_logs_zip(payload, RUN_ID)
        assert "empty" in text.lower() or "0" in text


class TestFormatFailureSummary:
    def test_includes_total_error_rows(self) -> None:
        payload = {
            "total_error_rows": 42,
            "jobs_with_errors": 2,
            "jobs_unavailable": [],
            "top_errors": [{"error": "ERR_A", "count": 30}, {"error": "ERR_B", "count": 12}],
        }
        text = format_failure_summary(payload)
        assert "42" in text

    def test_includes_top_errors(self) -> None:
        payload = {
            "total_error_rows": 5,
            "jobs_with_errors": 1,
            "jobs_unavailable": [],
            "top_errors": [{"error": "REQUIRED_FIELD_MISSING", "count": 5}],
        }
        text = format_failure_summary(payload)
        assert "REQUIRED_FIELD_MISSING" in text

    def test_no_errors_handled(self) -> None:
        payload = {
            "total_error_rows": 0,
            "jobs_with_errors": 0,
            "jobs_unavailable": [],
            "top_errors": [],
        }
        text = format_failure_summary(payload)
        assert "No error rows" in text

    def test_unavailable_jobs_mentioned(self) -> None:
        payload = {
            "total_error_rows": 0,
            "jobs_with_errors": 0,
            "jobs_unavailable": ["job-x", "job-y"],
            "top_errors": [],
        }
        text = format_failure_summary(payload)
        assert "job-x" in text or "unavailable" in text.lower()


# ── server.py dispatch ────────────────────────────────────────────────────────


def _get_registered_tools() -> list:
    """Register tools on a test Server instance and retrieve the full tool list."""
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


class TestServerDispatch:
    def test_preview_success_csv_registered(self) -> None:
        tools = _get_registered_tools()
        names = [t.name for t in tools]
        assert "preview_success_csv" in names

    def test_preview_error_csv_registered(self) -> None:
        tools = _get_registered_tools()
        names = [t.name for t in tools]
        assert "preview_error_csv" in names

    def test_preview_unprocessed_csv_registered(self) -> None:
        tools = _get_registered_tools()
        names = [t.name for t in tools]
        assert "preview_unprocessed_csv" in names

    def test_download_logs_zip_registered(self) -> None:
        tools = _get_registered_tools()
        names = [t.name for t in tools]
        assert "download_logs_zip" in names

    def test_failure_summary_registered(self) -> None:
        tools = _get_registered_tools()
        names = [t.name for t in tools]
        assert "failure_summary" in names

    def test_preview_tools_require_job_id(self) -> None:
        tools = _get_registered_tools()
        for name in ("preview_success_csv", "preview_error_csv", "preview_unprocessed_csv"):
            tool = next(t for t in tools if t.name == name)
            assert "job_id" in tool.inputSchema["required"]

    def test_download_logs_zip_requires_run_id(self) -> None:
        tools = _get_registered_tools()
        tool = next(t for t in tools if t.name == "download_logs_zip")
        assert "run_id" in tool.inputSchema["required"]

    def test_failure_summary_requires_job_ids(self) -> None:
        tools = _get_registered_tools()
        tool = next(t for t in tools if t.name == "failure_summary")
        assert "job_ids" in tool.inputSchema["required"]

    def test_no_curated_tools_are_todo(self) -> None:
        """All CURATED_TOOLS entries must have status='implemented'."""
        from sf_bulk_loader_mcp.server import CURATED_TOOLS
        todo_entries = [t for t in CURATED_TOOLS if t.get("status") != "implemented"]
        assert todo_entries == [], (
            f"The following CURATED_TOOLS entries still have TODO status: "
            f"{[t['name'] for t in todo_entries]}"
        )
