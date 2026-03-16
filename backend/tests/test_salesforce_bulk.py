"""Tests for app.services.salesforce_bulk.

Covers:
  - BulkAPIError attributes.
  - Context manager lifecycle (owns / borrows httpx client).
  - _request: success, 5xx retry with backoff, 429 retry with Retry-After,
    429 without Retry-After, network error retry, retries exhausted.
  - create_job: success, upsert with external_id_field, assignment_rule_id,
    non-200 error.
  - upload_csv: success (201), non-2xx error.
  - close_job: success, non-200 error.
  - poll_job: immediate terminal state, multi-poll backoff, Failed/Aborted
    terminal states, non-200 error.
  - get_success_results / get_failed_results / get_unprocessed_results:
    correct URLs, success, non-200 error.
  - abort_job: success, non-200 error.
"""

from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest

from app.services.salesforce_bulk import BulkAPIError, SalesforceBulkClient

# ── Helpers ──────────────────────────────────────────────────────────────────


INSTANCE_URL = "https://myorg.my.salesforce.com"
ACCESS_TOKEN = "test_bearer_token"
API_VERSION = "v62.0"
JOB_ID = "750R000000BulkJobId"


def make_response(
    status_code: int,
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
    resp.headers = headers or {}
    return resp


@pytest.fixture
def mock_http() -> AsyncMock:
    """A mock httpx.AsyncClient whose .request() can be configured per-test."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.fixture
def bulk_client(mock_http: AsyncMock) -> SalesforceBulkClient:
    """SalesforceBulkClient wired to the mock HTTP client."""
    return SalesforceBulkClient(
        INSTANCE_URL,
        ACCESS_TOKEN,
        api_version=API_VERSION,
        http_client=mock_http,
    )


# ── BulkAPIError ─────────────────────────────────────────────────────────────


class TestBulkAPIError:
    def test_message(self) -> None:
        err = BulkAPIError("something went wrong")
        assert str(err) == "something went wrong"

    def test_status_code_and_body(self) -> None:
        err = BulkAPIError("fail", status_code=503, body="Service Unavailable")
        assert err.status_code == 503
        assert err.body == "Service Unavailable"

    def test_defaults(self) -> None:
        err = BulkAPIError("fail")
        assert err.status_code is None
        assert err.body == ""


# ── Context manager ───────────────────────────────────────────────────────────


class TestContextManager:
    @pytest.mark.asyncio
    async def test_creates_and_closes_own_client(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "app.services.salesforce_bulk.httpx.AsyncClient",
            return_value=mock_client,
        ) as MockClass:
            async with SalesforceBulkClient(INSTANCE_URL, ACCESS_TOKEN) as client:
                assert client._client is mock_client

            MockClass.assert_called_once_with(timeout=30.0)
            mock_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_not_close_injected_client(self, mock_http: AsyncMock) -> None:
        async with SalesforceBulkClient(
            INSTANCE_URL, ACCESS_TOKEN, http_client=mock_http
        ) as client:
            assert client._client is mock_http

        mock_http.aclose.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_if_no_client_on_request(self) -> None:
        client = SalesforceBulkClient(INSTANCE_URL, ACCESS_TOKEN)
        # _client is None because we never entered __aenter__
        with pytest.raises(RuntimeError, match="no HTTP client"):
            await client._request("GET", "https://example.com")


# ── _request retry logic ─────────────────────────────────────────────────────


class TestRequest:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        ok = make_response(200, {"key": "val"})
        mock_http.request = AsyncMock(return_value=ok)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            resp = await bulk_client._request("GET", "https://example.com/test")

        assert resp.status_code == 200
        mock_sleep.assert_not_awaited()
        assert mock_http.request.await_count == 1

    @pytest.mark.asyncio
    async def test_auth_header_always_included(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(return_value=make_response(200))

        await bulk_client._request("GET", "https://example.com/test")

        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"

    @pytest.mark.asyncio
    async def test_extra_headers_merged(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(return_value=make_response(200))

        await bulk_client._request(
            "POST",
            "https://example.com/test",
            headers={"Content-Type": "application/json"},
        )

        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Content-Type"] == "application/json"
        assert "Authorization" in kwargs["headers"]

    @pytest.mark.asyncio
    async def test_5xx_retried_three_times(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        server_error = make_response(503, content=b"Service Unavailable")
        mock_http.request = AsyncMock(return_value=server_error)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(BulkAPIError, match="503"):
                await bulk_client._request("GET", "https://example.com/test")

        assert mock_http.request.await_count == 4  # 1 initial + 3 retries
        # Backoff: 1 s, 2 s, 4 s
        assert mock_sleep.await_args_list == [call(1.0), call(2.0), call(4.0)]

    @pytest.mark.asyncio
    async def test_5xx_succeeds_on_retry(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        fail = make_response(500)
        ok = make_response(200, {"id": "abc"})
        mock_http.request = AsyncMock(side_effect=[fail, ok])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            resp = await bulk_client._request("GET", "https://example.com/test")

        assert resp.status_code == 200
        assert mock_http.request.await_count == 2
        mock_sleep.assert_awaited_once_with(1.0)

    @pytest.mark.asyncio
    async def test_429_retried_with_retry_after_header(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        rate_limit = make_response(429, headers={"Retry-After": "15"})
        ok = make_response(200)
        mock_http.request = AsyncMock(side_effect=[rate_limit, ok])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            resp = await bulk_client._request("GET", "https://example.com/test")

        assert resp.status_code == 200
        mock_sleep.assert_awaited_once_with(15.0)

    @pytest.mark.asyncio
    async def test_429_retried_with_backoff_when_no_retry_after(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        rate_limit = make_response(429, headers={})  # No Retry-After
        ok = make_response(200)
        mock_http.request = AsyncMock(side_effect=[rate_limit, ok])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            resp = await bulk_client._request("GET", "https://example.com/test")

        assert resp.status_code == 200
        mock_sleep.assert_awaited_once_with(1.0)  # 2^0 = 1 s

    @pytest.mark.asyncio
    async def test_429_exhausted_raises(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        rate_limit = make_response(429, headers={})
        mock_http.request = AsyncMock(return_value=rate_limit)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(BulkAPIError, match="429"):
                await bulk_client._request("GET", "https://example.com/test")

        assert mock_http.request.await_count == 4

    @pytest.mark.asyncio
    async def test_network_error_retried(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        network_err = httpx.ConnectError("connection refused")
        ok = make_response(200)
        mock_http.request = AsyncMock(side_effect=[network_err, ok])

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            resp = await bulk_client._request("GET", "https://example.com/test")

        assert resp.status_code == 200
        mock_sleep.assert_awaited_once_with(1.0)

    @pytest.mark.asyncio
    async def test_network_error_exhausted_raises(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(BulkAPIError, match="Network error"):
                await bulk_client._request("GET", "https://example.com/test")

        assert mock_http.request.await_count == 4

    @pytest.mark.asyncio
    async def test_4xx_not_retried(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        client_error = make_response(400, content=b"Bad Request")
        mock_http.request = AsyncMock(return_value=client_error)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            resp = await bulk_client._request("GET", "https://example.com/test")

        assert resp.status_code == 400
        mock_sleep.assert_not_awaited()
        assert mock_http.request.await_count == 1


# ── create_job ────────────────────────────────────────────────────────────────


class TestCreateJob:
    @pytest.mark.asyncio
    async def test_success_returns_job_id(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"id": JOB_ID, "state": "Open"})
        )

        job_id = await bulk_client.create_job("Account", "insert")

        assert job_id == JOB_ID

    @pytest.mark.asyncio
    async def test_posts_to_ingest_base(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"id": JOB_ID})
        )

        await bulk_client.create_job("Account", "insert")

        args, kwargs = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1].endswith(f"/services/data/{API_VERSION}/jobs/ingest")

    @pytest.mark.asyncio
    async def test_request_body_fields(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"id": JOB_ID})
        )

        await bulk_client.create_job("Contact", "insert")

        _, kwargs = mock_http.request.call_args
        body = kwargs["json"]
        assert body["object"] == "Contact"
        assert body["operation"] == "insert"
        assert body["contentType"] == "CSV"
        assert body["lineEnding"] == "LF"

    @pytest.mark.asyncio
    async def test_upsert_includes_external_id_field(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"id": JOB_ID})
        )

        await bulk_client.create_job(
            "Account", "upsert", external_id_field="ExternalId__c"
        )

        _, kwargs = mock_http.request.call_args
        assert kwargs["json"]["externalIdFieldName"] == "ExternalId__c"

    @pytest.mark.asyncio
    async def test_assignment_rule_id_included(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"id": JOB_ID})
        )

        await bulk_client.create_job(
            "Lead", "insert", assignment_rule_id="01Q000000000001"
        )

        _, kwargs = mock_http.request.call_args
        assert kwargs["json"]["assignmentRuleId"] == "01Q000000000001"

    @pytest.mark.asyncio
    async def test_no_external_id_field_omitted(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"id": JOB_ID})
        )

        await bulk_client.create_job("Account", "insert")

        _, kwargs = mock_http.request.call_args
        assert "externalIdFieldName" not in kwargs["json"]

    @pytest.mark.asyncio
    async def test_non_200_raises_bulk_api_error(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(400, content=b"Bad request")
        )

        with pytest.raises(BulkAPIError, match="400"):
            await bulk_client.create_job("Account", "insert")

    @pytest.mark.asyncio
    async def test_content_type_header_set(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"id": JOB_ID})
        )

        await bulk_client.create_job("Account", "insert")

        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Content-Type"] == "application/json"


# ── upload_csv ────────────────────────────────────────────────────────────────


class TestUploadCSV:
    @pytest.mark.asyncio
    async def test_success_201(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(return_value=make_response(201))

        await bulk_client.upload_csv(JOB_ID, b"Name,Email\nAlice,a@b.com\n")

    @pytest.mark.asyncio
    async def test_success_204(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(return_value=make_response(204))
        await bulk_client.upload_csv(JOB_ID, b"Name\nAlice\n")

    @pytest.mark.asyncio
    async def test_puts_to_batches_url(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(return_value=make_response(201))
        csv_data = b"Name\nAlice\n"

        await bulk_client.upload_csv(JOB_ID, csv_data)

        args, kwargs = mock_http.request.call_args
        assert args[0] == "PUT"
        assert args[1].endswith(f"/jobs/ingest/{JOB_ID}/batches")

    @pytest.mark.asyncio
    async def test_content_type_is_text_csv(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(return_value=make_response(201))

        await bulk_client.upload_csv(JOB_ID, b"Name\n")

        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Content-Type"] == "text/csv"

    @pytest.mark.asyncio
    async def test_csv_bytes_sent_as_content(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(return_value=make_response(201))
        csv_data = b"Name,Age\nAlice,30\n"

        await bulk_client.upload_csv(JOB_ID, csv_data)

        _, kwargs = mock_http.request.call_args
        assert kwargs["content"] == csv_data

    @pytest.mark.asyncio
    async def test_non_2xx_raises_bulk_api_error(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(400, content=b"invalid")
        )

        with pytest.raises(BulkAPIError, match="400"):
            await bulk_client.upload_csv(JOB_ID, b"Name\n")


# ── close_job ─────────────────────────────────────────────────────────────────


class TestCloseJob:
    @pytest.mark.asyncio
    async def test_success(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"state": "UploadComplete"})
        )

        await bulk_client.close_job(JOB_ID)

    @pytest.mark.asyncio
    async def test_patches_upload_complete_state(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"state": "UploadComplete"})
        )

        await bulk_client.close_job(JOB_ID)

        args, kwargs = mock_http.request.call_args
        assert args[0] == "PATCH"
        assert args[1].endswith(f"/jobs/ingest/{JOB_ID}")
        assert kwargs["json"] == {"state": "UploadComplete"}

    @pytest.mark.asyncio
    async def test_non_200_raises(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(return_value=make_response(404))

        with pytest.raises(BulkAPIError, match="404"):
            await bulk_client.close_job(JOB_ID)


# ── poll_job ──────────────────────────────────────────────────────────────────


class TestPollJob:
    @pytest.mark.asyncio
    async def test_immediate_job_complete(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(
                200,
                {"state": "JobComplete", "numberRecordsProcessed": 100, "numberRecordsFailed": 0},
            )
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            state = await bulk_client.poll_job(JOB_ID)

        assert state == "JobComplete"
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_polls_until_job_complete(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        responses = [
            make_response(200, {"state": "InProgress"}),
            make_response(200, {"state": "InProgress"}),
            make_response(200, {"state": "JobComplete"}),
        ]
        mock_http.request = AsyncMock(side_effect=responses)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with patch("app.services.salesforce_bulk.settings") as mock_settings:
                mock_settings.sf_poll_interval_initial = 5
                mock_settings.sf_poll_interval_max = 30
                state = await bulk_client.poll_job(JOB_ID)

        assert state == "JobComplete"
        assert mock_http.request.await_count == 3
        # First sleep = initial interval (5 s), second sleep = min(5*2, 30) = 10 s
        assert mock_sleep.await_args_list == [call(5.0), call(10.0)]

    @pytest.mark.asyncio
    async def test_backoff_capped_at_max(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        # Produce enough InProgress responses to exceed the 30 s cap.
        in_progress = make_response(200, {"state": "InProgress"})
        done = make_response(200, {"state": "JobComplete"})
        # 5 → 10 → 20 → 30 → 30 … then done
        mock_http.request = AsyncMock(
            side_effect=[in_progress] * 5 + [done]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with patch(
                "app.services.salesforce_bulk.settings"
            ) as mock_settings:
                mock_settings.sf_poll_interval_initial = 5
                mock_settings.sf_poll_interval_max = 30
                await bulk_client.poll_job(JOB_ID)

        sleep_durations = [c.args[0] for c in mock_sleep.await_args_list]
        assert sleep_durations == [5.0, 10.0, 20.0, 30.0, 30.0]

    @pytest.mark.asyncio
    async def test_failed_state_returned(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"state": "Failed"})
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            state = await bulk_client.poll_job(JOB_ID)

        assert state == "Failed"

    @pytest.mark.asyncio
    async def test_aborted_state_returned(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"state": "Aborted"})
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            state = await bulk_client.poll_job(JOB_ID)

        assert state == "Aborted"

    @pytest.mark.asyncio
    async def test_polls_correct_url(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"state": "JobComplete"})
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await bulk_client.poll_job(JOB_ID)

        args, _ = mock_http.request.call_args
        assert args[0] == "GET"
        assert args[1].endswith(f"/jobs/ingest/{JOB_ID}")

    @pytest.mark.asyncio
    async def test_non_200_raises(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        # Use a 4xx so _request returns it without retrying, letting poll_job
        # raise its own "poll_job failed" error.
        mock_http.request = AsyncMock(return_value=make_response(404))

        with pytest.raises(BulkAPIError, match="poll_job failed"):
            await bulk_client.poll_job(JOB_ID)


# ── Results endpoints ─────────────────────────────────────────────────────────


class TestGetSuccessResults:
    @pytest.mark.asyncio
    async def test_returns_csv_bytes(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        csv_data = b"Id,Success\n001xxx,true\n"
        mock_http.request = AsyncMock(
            return_value=make_response(200, content=csv_data)
        )

        result = await bulk_client.get_success_results(JOB_ID)

        assert result == csv_data

    @pytest.mark.asyncio
    async def test_correct_url(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, content=b"data")
        )

        await bulk_client.get_success_results(JOB_ID)

        args, _ = mock_http.request.call_args
        assert args[1].endswith(f"/jobs/ingest/{JOB_ID}/successfulResults")

    @pytest.mark.asyncio
    async def test_non_200_raises(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(return_value=make_response(404))

        with pytest.raises(BulkAPIError, match="successfulResults"):
            await bulk_client.get_success_results(JOB_ID)


class TestGetFailedResults:
    @pytest.mark.asyncio
    async def test_correct_url(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, content=b"errors")
        )

        await bulk_client.get_failed_results(JOB_ID)

        args, _ = mock_http.request.call_args
        assert args[1].endswith(f"/jobs/ingest/{JOB_ID}/failedResults")

    @pytest.mark.asyncio
    async def test_returns_bytes(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        csv_data = b"Id,Error\nNULL,REQUIRED_FIELD_MISSING\n"
        mock_http.request = AsyncMock(
            return_value=make_response(200, content=csv_data)
        )

        result = await bulk_client.get_failed_results(JOB_ID)

        assert result == csv_data

    @pytest.mark.asyncio
    async def test_non_200_raises(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        # Use 4xx so _request returns without retrying; _fetch_results raises.
        mock_http.request = AsyncMock(return_value=make_response(404))

        with pytest.raises(BulkAPIError, match="failedResults"):
            await bulk_client.get_failed_results(JOB_ID)


class TestGetUnprocessedResults:
    @pytest.mark.asyncio
    async def test_correct_url(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, content=b"unproc")
        )

        await bulk_client.get_unprocessed_results(JOB_ID)

        args, _ = mock_http.request.call_args
        assert args[1].endswith(f"/jobs/ingest/{JOB_ID}/unprocessedrecords")

    @pytest.mark.asyncio
    async def test_returns_bytes(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        csv_data = b"Name\nBob\n"
        mock_http.request = AsyncMock(
            return_value=make_response(200, content=csv_data)
        )

        result = await bulk_client.get_unprocessed_results(JOB_ID)

        assert result == csv_data

    @pytest.mark.asyncio
    async def test_non_200_raises(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        # Use 4xx so _request returns without retrying; _fetch_results raises.
        mock_http.request = AsyncMock(return_value=make_response(404))

        with pytest.raises(BulkAPIError, match="unprocessedrecords"):
            await bulk_client.get_unprocessed_results(JOB_ID)


# ── abort_job ─────────────────────────────────────────────────────────────────


class TestAbortJob:
    @pytest.mark.asyncio
    async def test_success(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"state": "Aborted"})
        )

        await bulk_client.abort_job(JOB_ID)

    @pytest.mark.asyncio
    async def test_patches_aborted_state(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"state": "Aborted"})
        )

        await bulk_client.abort_job(JOB_ID)

        args, kwargs = mock_http.request.call_args
        assert args[0] == "PATCH"
        assert args[1].endswith(f"/jobs/ingest/{JOB_ID}")
        assert kwargs["json"] == {"state": "Aborted"}

    @pytest.mark.asyncio
    async def test_content_type_header(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"state": "Aborted"})
        )

        await bulk_client.abort_job(JOB_ID)

        _, kwargs = mock_http.request.call_args
        assert kwargs["headers"]["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_non_200_raises(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(return_value=make_response(400))

        with pytest.raises(BulkAPIError, match="400"):
            await bulk_client.abort_job(JOB_ID)


# ── poll_job_once ─────────────────────────────────────────────────────────────


class TestPollJobOnce:
    @pytest.mark.asyncio
    async def test_returns_state_and_counts(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(
                200,
                {"state": "InProgress", "numberRecordsProcessed": 50, "numberRecordsFailed": 2},
            )
        )

        state, processed, failed = await bulk_client.poll_job_once(JOB_ID)

        assert state == "InProgress"
        assert processed == 50
        assert failed == 2

    @pytest.mark.asyncio
    async def test_terminal_state_returned(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(
                200,
                {"state": "JobComplete", "numberRecordsProcessed": 100, "numberRecordsFailed": 0},
            )
        )

        state, processed, failed = await bulk_client.poll_job_once(JOB_ID)

        assert state == "JobComplete"
        assert processed == 100
        assert failed == 0

    @pytest.mark.asyncio
    async def test_non_200_raises(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(return_value=make_response(404))

        with pytest.raises(BulkAPIError, match="poll_job_once failed"):
            await bulk_client.poll_job_once(JOB_ID)

    @pytest.mark.asyncio
    async def test_polls_correct_url(
        self, bulk_client: SalesforceBulkClient, mock_http: AsyncMock
    ) -> None:
        mock_http.request = AsyncMock(
            return_value=make_response(200, {"state": "JobComplete"})
        )

        await bulk_client.poll_job_once(JOB_ID)

        args, _ = mock_http.request.call_args
        assert args[0] == "GET"
        assert args[1].endswith(f"/jobs/ingest/{JOB_ID}")


# ── URL construction sanity checks ───────────────────────────────────────────


class TestURLConstruction:
    def test_ingest_base_url(self) -> None:
        c = SalesforceBulkClient(
            "https://myorg.my.salesforce.com/",
            "tok",
            api_version="v62.0",
            http_client=MagicMock(),
        )
        # Trailing slash on instance_url should be stripped
        assert c._ingest_base == (
            "https://myorg.my.salesforce.com"
            "/services/data/v62.0/jobs/ingest"
        )

    def test_job_url(self) -> None:
        c = SalesforceBulkClient(
            INSTANCE_URL,
            "tok",
            api_version="v62.0",
            http_client=MagicMock(),
        )
        assert c._job_url("ABC123") == (
            f"{INSTANCE_URL}/services/data/v62.0/jobs/ingest/ABC123"
        )
