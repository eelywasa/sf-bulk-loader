"""Tests for app.services.bulk_query_executor (SFBL-169).

Coverage:
- Single-page happy path: returns expected row count, writes well-formed CSV.
- Multi-page locator pagination: three pages concatenated correctly; header
  appears exactly once.
- Empty result: header-only CSV written; row count 0; returns JobComplete state.
- JobFailed terminal state: raises BulkQueryJobFailed with correct final_state.
- Transient 503 on polling: triggers retry and eventually succeeds.
- 429 on results fetch: triggers retry with Retry-After and eventually succeeds.
- Integration-style test: full executor path against LocalOutputStorage, asserts
  concatenated CSV is well-formed on disk.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import os
os.environ.setdefault("SFBL_DISABLE_ENV_FILE", "1")

from app.services.bulk_query_executor import (
    BulkQueryError,
    BulkQueryJobFailed,
    BulkQueryResult,
    run_bulk_query,
)
from app.services.output_storage import LocalOutputStorage

# ── Constants ─────────────────────────────────────────────────────────────────

INSTANCE_URL = "https://myorg.my.salesforce.com"
ACCESS_TOKEN = "test_access_token"
API_VERSION = "v62.0"
JOB_ID = "7509000000AbcQueryJob"
SOQL = "SELECT Id, Name FROM Account"

QUERY_BASE = f"{INSTANCE_URL}/services/data/{API_VERSION}/jobs/query"
JOB_URL = f"{QUERY_BASE}/{JOB_ID}"
RESULTS_URL = f"{JOB_URL}/results"

HEADER = b"Id,Name\n"
ROW_1 = b"001000000001,Alpha Corp\n"
ROW_2 = b"001000000002,Beta Ltd\n"
ROW_3 = b"001000000003,Gamma Inc\n"

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_response(
    status_code: int,
    *,
    json_data: dict | None = None,
    content: bytes = b"",
    headers: dict | None = None,
) -> MagicMock:
    """Build a minimal httpx.Response-like mock."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.content = content
    resp.text = content.decode("utf-8", errors="replace") if content else ""
    resp.headers = httpx.Headers(headers or {})
    return resp


async def _aiter_bytes_from(data: bytes, chunk_size: int = 64):
    """Yield *data* in chunks, simulating httpx's aiter_bytes."""
    for i in range(0, max(1, len(data)), chunk_size):
        yield data[i : i + chunk_size]
    if not data:
        yield b""


def make_streaming_response(
    status_code: int,
    content: bytes,
    *,
    locator: str | None = None,
    extra_headers: dict | None = None,
) -> MagicMock:
    """Build a mock httpx streaming response context manager."""
    resp = MagicMock()
    resp.status_code = status_code

    hdrs: dict[str, str] = {}
    if locator is not None:
        hdrs["Sforce-Locator"] = locator
    else:
        hdrs["Sforce-Locator"] = "null"
    if extra_headers:
        hdrs.update(extra_headers)
    resp.headers = httpx.Headers(hdrs)

    resp.aiter_bytes = MagicMock(return_value=_aiter_bytes_from(content))
    resp.aread = AsyncMock(return_value=content)

    # Make it work as an async context manager.
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)

    return resp


@pytest.fixture
def mock_http() -> MagicMock:
    """A mock httpx.AsyncClient."""
    client = MagicMock(spec=httpx.AsyncClient)
    client.aclose = AsyncMock()
    return client


def _make_create_job_resp() -> MagicMock:
    return make_response(200, json_data={"id": JOB_ID})


def _make_poll_complete_resp() -> MagicMock:
    return make_response(
        200, json_data={"state": "JobComplete", "numberRecordsProcessed": 2}
    )


# ── Single-page happy path ────────────────────────────────────────────────────


class TestSinglePage:
    @pytest.mark.asyncio
    async def test_single_page_returns_result(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """Single page: correct row count, bytes, state, artefact_uri."""
        page_content = HEADER + ROW_1 + ROW_2
        stream_resp = make_streaming_response(200, page_content, locator=None)

        mock_http.request = AsyncMock(
            side_effect=[
                _make_create_job_resp(),
                _make_poll_complete_resp(),
            ]
        )
        mock_http.stream = MagicMock(return_value=stream_resp)

        storage = LocalOutputStorage(str(tmp_path))
        relative_path = "run1/01-Account-20260101T000000.csv"

        result = await run_bulk_query(
            soql=SOQL,
            operation="query",
            instance_url=INSTANCE_URL,
            access_token=ACCESS_TOKEN,
            output_storage=storage,
            relative_path=relative_path,
            api_version=API_VERSION,
            http_client=mock_http,
        )

        assert isinstance(result, BulkQueryResult)
        assert result.final_state == "JobComplete"
        assert result.row_count == 2
        assert result.byte_count == len(page_content)
        assert result.artefact_uri == relative_path

    @pytest.mark.asyncio
    async def test_single_page_csv_on_disk(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """Single page: the written file has correct CSV content."""
        page_content = HEADER + ROW_1 + ROW_2
        stream_resp = make_streaming_response(200, page_content, locator=None)

        mock_http.request = AsyncMock(
            side_effect=[_make_create_job_resp(), _make_poll_complete_resp()]
        )
        mock_http.stream = MagicMock(return_value=stream_resp)

        storage = LocalOutputStorage(str(tmp_path))
        relative_path = "run1/step.csv"

        await run_bulk_query(
            soql=SOQL,
            operation="query",
            instance_url=INSTANCE_URL,
            access_token=ACCESS_TOKEN,
            output_storage=storage,
            relative_path=relative_path,
            api_version=API_VERSION,
            http_client=mock_http,
        )

        written = storage.read_bytes(relative_path)
        assert written == page_content


# ── Multi-page locator pagination ─────────────────────────────────────────────


class TestMultiPagePagination:
    @pytest.mark.asyncio
    async def test_three_pages_header_once(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """Three pages: header appears exactly once in the output file."""
        page1 = HEADER + ROW_1
        page2 = HEADER + ROW_2  # header will be stripped
        page3 = HEADER + ROW_3  # header will be stripped

        locator_1 = "LOCATOR_A"
        locator_2 = "LOCATOR_B"

        stream_p1 = make_streaming_response(200, page1, locator=locator_1)
        stream_p2 = make_streaming_response(200, page2, locator=locator_2)
        stream_p3 = make_streaming_response(200, page3, locator=None)

        mock_http.request = AsyncMock(
            side_effect=[_make_create_job_resp(), _make_poll_complete_resp()]
        )
        # stream is called once per page
        mock_http.stream = MagicMock(side_effect=[stream_p1, stream_p2, stream_p3])

        storage = LocalOutputStorage(str(tmp_path))
        relative_path = "run1/multi.csv"

        result = await run_bulk_query(
            soql=SOQL,
            operation="query",
            instance_url=INSTANCE_URL,
            access_token=ACCESS_TOKEN,
            output_storage=storage,
            relative_path=relative_path,
            api_version=API_VERSION,
            http_client=mock_http,
        )

        written = storage.read_bytes(relative_path)
        # The expected file is header + row1 + row2 + row3 (headers from p2/p3 stripped).
        expected = HEADER + ROW_1 + ROW_2 + ROW_3
        assert written == expected
        assert result.row_count == 3
        # stream was called 3 times (one per page)
        assert mock_http.stream.call_count == 3

    @pytest.mark.asyncio
    async def test_locator_passed_as_query_param(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """Locator from page 1 is forwarded as ``locator`` query param on page 2."""
        locator_val = "LOCATOR_X"
        stream_p1 = make_streaming_response(200, HEADER + ROW_1, locator=locator_val)
        stream_p2 = make_streaming_response(200, HEADER + ROW_2, locator=None)

        mock_http.request = AsyncMock(
            side_effect=[_make_create_job_resp(), _make_poll_complete_resp()]
        )
        mock_http.stream = MagicMock(side_effect=[stream_p1, stream_p2])

        storage = LocalOutputStorage(str(tmp_path))
        await run_bulk_query(
            soql=SOQL,
            operation="query",
            instance_url=INSTANCE_URL,
            access_token=ACCESS_TOKEN,
            output_storage=storage,
            relative_path="run/step.csv",
            api_version=API_VERSION,
            http_client=mock_http,
        )

        # Second stream call should include locator param
        second_call_kwargs = mock_http.stream.call_args_list[1]
        params = second_call_kwargs.kwargs.get("params") or second_call_kwargs[1].get("params", {})
        assert params.get("locator") == locator_val


# ── Empty result ──────────────────────────────────────────────────────────────


class TestEmptyResult:
    @pytest.mark.asyncio
    async def test_empty_result_header_only(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """Zero-row result: header-only file written; row_count == 0."""
        # Salesforce returns just the header when there are no results.
        empty_content = HEADER  # only header, no data rows
        stream_resp = make_streaming_response(200, empty_content, locator=None)

        mock_http.request = AsyncMock(
            side_effect=[
                _make_create_job_resp(),
                make_response(
                    200,
                    json_data={"state": "JobComplete", "numberRecordsProcessed": 0},
                ),
            ]
        )
        mock_http.stream = MagicMock(return_value=stream_resp)

        storage = LocalOutputStorage(str(tmp_path))
        relative_path = "run/empty.csv"

        result = await run_bulk_query(
            soql=SOQL,
            operation="query",
            instance_url=INSTANCE_URL,
            access_token=ACCESS_TOKEN,
            output_storage=storage,
            relative_path=relative_path,
            api_version=API_VERSION,
            http_client=mock_http,
        )

        assert result.row_count == 0
        assert result.final_state == "JobComplete"

        written = storage.read_bytes(relative_path)
        assert written == HEADER

    @pytest.mark.asyncio
    async def test_empty_result_byte_count(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """byte_count should equal the length of the header-only response."""
        empty_content = HEADER
        stream_resp = make_streaming_response(200, empty_content, locator=None)

        mock_http.request = AsyncMock(
            side_effect=[
                _make_create_job_resp(),
                make_response(200, json_data={"state": "JobComplete", "numberRecordsProcessed": 0}),
            ]
        )
        mock_http.stream = MagicMock(return_value=stream_resp)

        storage = LocalOutputStorage(str(tmp_path))
        result = await run_bulk_query(
            soql=SOQL,
            operation="query",
            instance_url=INSTANCE_URL,
            access_token=ACCESS_TOKEN,
            output_storage=storage,
            relative_path="run/e.csv",
            api_version=API_VERSION,
            http_client=mock_http,
        )

        assert result.byte_count == len(HEADER)


# ── JobFailed / Aborted terminal state ───────────────────────────────────────


class TestJobFailed:
    @pytest.mark.asyncio
    async def test_job_failed_raises_bulk_query_job_failed(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """When Salesforce returns state=Failed, BulkQueryJobFailed is raised."""
        mock_http.request = AsyncMock(
            side_effect=[
                _make_create_job_resp(),
                make_response(
                    200,
                    json_data={"state": "Failed", "numberRecordsProcessed": 0},
                ),
            ]
        )

        storage = LocalOutputStorage(str(tmp_path))
        with pytest.raises(BulkQueryJobFailed) as exc_info:
            await run_bulk_query(
                soql=SOQL,
                operation="query",
                instance_url=INSTANCE_URL,
                access_token=ACCESS_TOKEN,
                output_storage=storage,
                relative_path="run/failed.csv",
                api_version=API_VERSION,
                http_client=mock_http,
            )

        assert exc_info.value.final_state == "Failed"

    @pytest.mark.asyncio
    async def test_job_aborted_raises_bulk_query_job_failed(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """When Salesforce returns state=Aborted, BulkQueryJobFailed is raised."""
        mock_http.request = AsyncMock(
            side_effect=[
                _make_create_job_resp(),
                make_response(
                    200,
                    json_data={"state": "Aborted", "numberRecordsProcessed": 0},
                ),
            ]
        )

        storage = LocalOutputStorage(str(tmp_path))
        with pytest.raises(BulkQueryJobFailed) as exc_info:
            await run_bulk_query(
                soql=SOQL,
                operation="query",
                instance_url=INSTANCE_URL,
                access_token=ACCESS_TOKEN,
                output_storage=storage,
                relative_path="run/aborted.csv",
                api_version=API_VERSION,
                http_client=mock_http,
            )

        assert exc_info.value.final_state == "Aborted"

    @pytest.mark.asyncio
    async def test_bulk_query_job_failed_is_subclass_of_bulk_query_error(self) -> None:
        """BulkQueryJobFailed is a BulkQueryError subclass."""
        exc = BulkQueryJobFailed("fail", final_state="Failed")
        assert isinstance(exc, BulkQueryError)
        assert exc.final_state == "Failed"


# ── Transient 503 on polling triggers retry ───────────────────────────────────


class TestPollingRetry:
    @pytest.mark.asyncio
    async def test_transient_503_on_poll_triggers_retry(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """503 during polling retries and eventually succeeds."""
        poll_503 = make_response(503, content=b"Service Unavailable")

        page_content = HEADER + ROW_1
        stream_resp = make_streaming_response(200, page_content, locator=None)

        mock_http.request = AsyncMock(
            side_effect=[
                _make_create_job_resp(),  # create_job
                poll_503,                  # first poll → 503
                _make_poll_complete_resp(),  # second poll → JobComplete
            ]
        )
        mock_http.stream = MagicMock(return_value=stream_resp)

        storage = LocalOutputStorage(str(tmp_path))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await run_bulk_query(
                soql=SOQL,
                operation="query",
                instance_url=INSTANCE_URL,
                access_token=ACCESS_TOKEN,
                output_storage=storage,
                relative_path="run/retry.csv",
                api_version=API_VERSION,
                http_client=mock_http,
            )

        assert result.final_state == "JobComplete"
        # asyncio.sleep was called at least once for the 503 retry backoff.
        assert mock_sleep.await_count >= 1

    @pytest.mark.asyncio
    async def test_persistent_503_on_poll_raises_after_retries(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """503 on every poll attempt raises BulkQueryError after max retries."""
        poll_503 = make_response(503, content=b"Service Unavailable")
        # Need MAX_RETRIES + 1 = 4 503 responses.
        create_resp = _make_create_job_resp()
        mock_http.request = AsyncMock(
            side_effect=[create_resp, poll_503, poll_503, poll_503, poll_503]
        )

        storage = LocalOutputStorage(str(tmp_path))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(BulkQueryError):
                await run_bulk_query(
                    soql=SOQL,
                    operation="query",
                    instance_url=INSTANCE_URL,
                    access_token=ACCESS_TOKEN,
                    output_storage=storage,
                    relative_path="run/persist503.csv",
                    api_version=API_VERSION,
                    http_client=mock_http,
                )


# ── 429 on results fetch ───────────────────────────────────────────────────────


class TestResultsFetch429:
    @pytest.mark.asyncio
    async def test_429_on_results_triggers_retry(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """429 on results page triggers a retry that eventually succeeds."""
        page_content = HEADER + ROW_1

        rate_limited_resp = make_streaming_response(
            429, b"", locator=None, extra_headers={"Retry-After": "1"}
        )
        success_resp = make_streaming_response(200, page_content, locator=None)

        mock_http.request = AsyncMock(
            side_effect=[_make_create_job_resp(), _make_poll_complete_resp()]
        )
        mock_http.stream = MagicMock(side_effect=[rate_limited_resp, success_resp])

        storage = LocalOutputStorage(str(tmp_path))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await run_bulk_query(
                soql=SOQL,
                operation="query",
                instance_url=INSTANCE_URL,
                access_token=ACCESS_TOKEN,
                output_storage=storage,
                relative_path="run/rl.csv",
                api_version=API_VERSION,
                http_client=mock_http,
            )

        assert result.final_state == "JobComplete"
        assert result.row_count == 1
        # Sleep was called at least once for the retry.
        assert mock_sleep.await_count >= 1

    @pytest.mark.asyncio
    async def test_persistent_429_on_results_raises_after_retries(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """Persistent 429 on results raises BulkQueryError after max retries."""
        rate_limited_resp = make_streaming_response(429, b"", locator=None)

        mock_http.request = AsyncMock(
            side_effect=[_make_create_job_resp(), _make_poll_complete_resp()]
        )
        # Need MAX_RETRIES + 1 = 4 rate-limit responses.
        mock_http.stream = MagicMock(
            side_effect=[rate_limited_resp] * 4
        )

        storage = LocalOutputStorage(str(tmp_path))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(BulkQueryError):
                await run_bulk_query(
                    soql=SOQL,
                    operation="query",
                    instance_url=INSTANCE_URL,
                    access_token=ACCESS_TOKEN,
                    output_storage=storage,
                    relative_path="run/rl_fail.csv",
                    api_version=API_VERSION,
                    http_client=mock_http,
                )


# ── Integration-style test (LocalOutputStorage on disk) ───────────────────────


class TestIntegration:
    """Integration-style tests running the full executor against LocalOutputStorage."""

    @pytest.mark.asyncio
    async def test_full_executor_single_page_on_disk(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """Full path: single-page, file on disk has exactly one header row."""
        page_content = HEADER + ROW_1 + ROW_2
        stream_resp = make_streaming_response(200, page_content, locator=None)

        mock_http.request = AsyncMock(
            side_effect=[_make_create_job_resp(), _make_poll_complete_resp()]
        )
        mock_http.stream = MagicMock(return_value=stream_resp)

        storage = LocalOutputStorage(str(tmp_path))
        relative_path = "run42/01-Account-20260101T000000.csv"

        result = await run_bulk_query(
            soql=SOQL,
            operation="query",
            instance_url=INSTANCE_URL,
            access_token=ACCESS_TOKEN,
            output_storage=storage,
            relative_path=relative_path,
            api_version=API_VERSION,
            http_client=mock_http,
        )

        # File exists on disk at expected path.
        import pathlib
        dest = pathlib.Path(tmp_path) / relative_path
        assert dest.exists(), f"Expected output file at {dest}"

        content = dest.read_bytes()
        lines = content.split(b"\n")
        non_empty = [ln for ln in lines if ln]
        # First line should be the header.
        assert non_empty[0] == b"Id,Name"
        # Remaining lines are data rows.
        assert len(non_empty) == 3  # header + 2 data rows

        assert result.row_count == 2
        assert result.final_state == "JobComplete"

    @pytest.mark.asyncio
    async def test_full_executor_multi_page_on_disk(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """Full path: multi-page, final file on disk is a valid single-header CSV."""
        page1 = HEADER + ROW_1
        page2 = HEADER + ROW_2
        page3 = HEADER + ROW_3

        stream_p1 = make_streaming_response(200, page1, locator="LOC_1")
        stream_p2 = make_streaming_response(200, page2, locator="LOC_2")
        stream_p3 = make_streaming_response(200, page3, locator=None)

        mock_http.request = AsyncMock(
            side_effect=[_make_create_job_resp(), _make_poll_complete_resp()]
        )
        mock_http.stream = MagicMock(side_effect=[stream_p1, stream_p2, stream_p3])

        storage = LocalOutputStorage(str(tmp_path))
        relative_path = "run42/02-Contact-multi.csv"

        result = await run_bulk_query(
            soql=SOQL,
            operation="queryAll",
            instance_url=INSTANCE_URL,
            access_token=ACCESS_TOKEN,
            output_storage=storage,
            relative_path=relative_path,
            api_version=API_VERSION,
            http_client=mock_http,
        )

        import pathlib
        content = (pathlib.Path(tmp_path) / relative_path).read_bytes()
        expected = HEADER + ROW_1 + ROW_2 + ROW_3
        assert content == expected

        # Header appears exactly once.
        assert content.count(b"Id,Name\n") == 1

        assert result.row_count == 3

    @pytest.mark.asyncio
    async def test_create_job_posts_correct_payload(
        self, mock_http: MagicMock, tmp_path
    ) -> None:
        """Verify the POST to jobs/query uses the correct operation and query body."""
        stream_resp = make_streaming_response(200, HEADER + ROW_1, locator=None)

        mock_http.request = AsyncMock(
            side_effect=[_make_create_job_resp(), _make_poll_complete_resp()]
        )
        mock_http.stream = MagicMock(return_value=stream_resp)

        storage = LocalOutputStorage(str(tmp_path))
        await run_bulk_query(
            soql=SOQL,
            operation="queryAll",
            instance_url=INSTANCE_URL,
            access_token=ACCESS_TOKEN,
            output_storage=storage,
            relative_path="run/p.csv",
            api_version=API_VERSION,
            http_client=mock_http,
        )

        # First request call is create_job (POST).
        create_call = mock_http.request.call_args_list[0]
        method = create_call.args[0] if create_call.args else create_call.kwargs.get("method")
        url = create_call.args[1] if len(create_call.args) > 1 else create_call.kwargs.get("url")
        body = create_call.kwargs.get("json", {})

        assert method == "POST"
        assert url == QUERY_BASE
        assert body["operation"] == "queryAll"
        assert body["query"] == SOQL
