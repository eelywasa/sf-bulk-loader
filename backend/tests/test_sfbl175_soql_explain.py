"""Tests for SFBL-175: Preflight SOQL validation via Salesforce explain endpoint.

Covers:
- Unit tests for explain_soql helper:
    - Valid SOQL returns SoqlExplainResult(valid=True, plan={...})
    - 400 MALFORMED_QUERY surfaces message verbatim
    - 400 INVALID_FIELD surfaces message verbatim
    - 5xx triggers retry and eventually succeeds
    - 5xx retries exhausted raises BulkAPIError
    - 429 triggers retry with Retry-After header
- Integration tests for preview route:
    - query step with valid SOQL returns kind="query", valid=True, plan
    - query step with invalid SOQL returns kind="query", valid=False, error
    - DML step still returns file-preview envelope (regression guard)
    - query step with no connection returns 404
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch, call

import httpx
import pytest

from app.services.salesforce_query_validation import (
    SoqlExplainResult,
    _extract_sf_error,
    explain_soql,
)
from app.services.salesforce_bulk import BulkAPIError

# ── Shared constants ─────────────────────────────────────────────────────────

INSTANCE_URL = "https://myorg.my.salesforce.com"
ACCESS_TOKEN = "test_access_token"
API_VERSION = "v62.0"
VALID_SOQL = "SELECT Id, Name FROM Account"

# ── Response mock helpers ────────────────────────────────────────────────────


def make_response(
    status_code: int,
    json_data=None,
    text: str = "",
    headers: dict | None = None,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    resp.headers = headers or {}
    return resp


def make_200_response() -> MagicMock:
    return make_response(
        200,
        json_data={
            "plans": [
                {
                    "leadingOperationType": "TableScan",
                    "sobjectType": "Account",
                    "cost": 1.0,
                    "relativeCost": 1.5,
                }
            ]
        },
    )


def make_400_malformed() -> MagicMock:
    return make_response(
        400,
        json_data=[{"message": "MALFORMED_QUERY: unexpected token: FROMM", "errorCode": "MALFORMED_QUERY"}],
        text='[{"message":"MALFORMED_QUERY: unexpected token: FROMM","errorCode":"MALFORMED_QUERY"}]',
    )


def make_400_invalid_field() -> MagicMock:
    return make_response(
        400,
        json_data=[{"message": "INVALID_FIELD: No such column 'Foo__c' on entity 'Account'", "errorCode": "INVALID_FIELD"}],
        text='[{"message":"INVALID_FIELD: No such column \'Foo__c\' on entity \'Account\'","errorCode":"INVALID_FIELD"}]',
    )


# ── Unit: SoqlExplainResult ──────────────────────────────────────────────────


class TestSoqlExplainResult:
    def test_valid_defaults(self) -> None:
        r = SoqlExplainResult(valid=True, plan={"leadingOperation": "TableScan"})
        assert r.valid is True
        assert r.error == ""

    def test_invalid_defaults(self) -> None:
        r = SoqlExplainResult(valid=False, error="some error")
        assert r.plan == {}
        assert r.valid is False


# ── Unit: _extract_sf_error ──────────────────────────────────────────────────


class TestExtractSfError:
    def test_extracts_message_from_array(self) -> None:
        resp = make_response(
            400,
            json_data=[{"message": "MALFORMED_QUERY: bad token", "errorCode": "MALFORMED_QUERY"}],
        )
        assert _extract_sf_error(resp) == "MALFORMED_QUERY: bad token"

    def test_extracts_message_from_dict(self) -> None:
        resp = make_response(400, json_data={"message": "some error"})
        assert _extract_sf_error(resp) == "some error"

    def test_falls_back_to_text_on_empty_array(self) -> None:
        resp = make_response(400, json_data=[], text="raw error body")
        assert _extract_sf_error(resp) == "raw error body"

    def test_falls_back_to_text_on_parse_failure(self) -> None:
        resp = MagicMock(spec=httpx.Response)
        resp.json.side_effect = ValueError("not JSON")
        resp.text = "not json at all"
        assert _extract_sf_error(resp) == "not json at all"

    def test_truncates_long_text(self) -> None:
        resp = make_response(400, json_data=[], text="x" * 1000)
        assert len(_extract_sf_error(resp)) <= 500


# ── Unit: explain_soql — injected client ────────────────────────────────────


class TestExplainSoqlValid:
    @pytest.mark.anyio
    async def test_valid_soql_returns_plan(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=make_200_response())

        result = await explain_soql(
            INSTANCE_URL, ACCESS_TOKEN, VALID_SOQL,
            api_version=API_VERSION,
            http_client=mock_client,
        )

        assert result.valid is True
        assert result.error == ""
        assert result.plan["leadingOperation"] == "TableScan"
        assert result.plan["sobjectType"] == "Account"
        assert result.plan["relativeCost"] == 1.5

    @pytest.mark.anyio
    async def test_valid_soql_empty_plans_returns_empty_plan_dict(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=make_response(200, json_data={"plans": []}))

        result = await explain_soql(
            INSTANCE_URL, ACCESS_TOKEN, VALID_SOQL,
            api_version=API_VERSION,
            http_client=mock_client,
        )

        assert result.valid is True
        assert result.plan == {}


class TestExplainSoql400:
    @pytest.mark.anyio
    async def test_malformed_query_surfaces_message(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=make_400_malformed())

        result = await explain_soql(
            INSTANCE_URL, ACCESS_TOKEN, "SELECT Id FROMM Account",
            api_version=API_VERSION,
            http_client=mock_client,
        )

        assert result.valid is False
        assert "MALFORMED_QUERY" in result.error
        assert "unexpected token: FROMM" in result.error

    @pytest.mark.anyio
    async def test_invalid_field_surfaces_message(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=make_400_invalid_field())

        result = await explain_soql(
            INSTANCE_URL, ACCESS_TOKEN, "SELECT Foo__c FROM Account",
            api_version=API_VERSION,
            http_client=mock_client,
        )

        assert result.valid is False
        assert "INVALID_FIELD" in result.error
        assert "Foo__c" in result.error

    @pytest.mark.anyio
    async def test_400_does_not_raise(self) -> None:
        """400 is an expected outcome — must not raise."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=make_400_malformed())

        # Should not raise:
        result = await explain_soql(
            INSTANCE_URL, ACCESS_TOKEN, "bad soql",
            api_version=API_VERSION,
            http_client=mock_client,
        )
        assert not result.valid


class TestExplainSoqlRetry:
    @pytest.mark.anyio
    async def test_5xx_retries_and_succeeds(self) -> None:
        """A single 5xx followed by a 200 should succeed after one retry."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                make_response(500, text="server error"),
                make_200_response(),
            ]
        )

        with patch("app.services.salesforce_query_validation.asyncio.sleep", new_callable=AsyncMock):
            result = await explain_soql(
                INSTANCE_URL, ACCESS_TOKEN, VALID_SOQL,
                api_version=API_VERSION,
                http_client=mock_client,
            )

        assert result.valid is True
        assert mock_client.get.call_count == 2

    @pytest.mark.anyio
    async def test_5xx_retries_exhausted_raises(self) -> None:
        """Persistent 5xx should raise BulkAPIError after _MAX_RETRIES retries."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=make_response(503, text="Service Unavailable"))

        with patch("app.services.salesforce_query_validation.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(BulkAPIError):
                await explain_soql(
                    INSTANCE_URL, ACCESS_TOKEN, VALID_SOQL,
                    api_version=API_VERSION,
                    http_client=mock_client,
                )

        # 1 initial attempt + 3 retries = 4 calls
        assert mock_client.get.call_count == 4

    @pytest.mark.anyio
    async def test_429_retries_with_retry_after(self) -> None:
        """429 with Retry-After header should honour it and retry."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                make_response(429, headers={"Retry-After": "2"}),
                make_200_response(),
            ]
        )

        sleep_mock = AsyncMock()
        with patch("app.services.salesforce_query_validation.asyncio.sleep", sleep_mock):
            result = await explain_soql(
                INSTANCE_URL, ACCESS_TOKEN, VALID_SOQL,
                api_version=API_VERSION,
                http_client=mock_client,
            )

        assert result.valid is True
        # Retry-After=2 should have been used as the sleep value
        sleep_mock.assert_awaited_once_with(2.0)

    @pytest.mark.anyio
    async def test_network_error_retries(self) -> None:
        """Network-level error should retry and succeed."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                httpx.ConnectError("connection refused"),
                make_200_response(),
            ]
        )

        with patch("app.services.salesforce_query_validation.asyncio.sleep", new_callable=AsyncMock):
            result = await explain_soql(
                INSTANCE_URL, ACCESS_TOKEN, VALID_SOQL,
                api_version=API_VERSION,
                http_client=mock_client,
            )

        assert result.valid is True

    @pytest.mark.anyio
    async def test_url_encoding_of_soql(self) -> None:
        """Spaces and special chars in SOQL must be URL-encoded."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=make_200_response())

        await explain_soql(
            INSTANCE_URL, ACCESS_TOKEN, "SELECT Id FROM Account WHERE Name = 'Test'",
            api_version=API_VERSION,
            http_client=mock_client,
        )

        called_url = mock_client.get.call_args[0][0]
        # Spaces and special chars must be encoded in the query string
        assert " " not in called_url
        assert "explain=" in called_url


# ── Integration: preview route ───────────────────────────────────────────────


# Connection fixture shared by integration tests (needs a real ENCRYPTION_KEY
# to store a private key, which is already set in conftest.py).
_CONN_PAYLOAD = {
    "name": "Test SF Org",
    "instance_url": "https://myorg.my.salesforce.com",
    "login_url": "https://login.salesforce.com",
    "client_id": "myclientid",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nFAKEKEY\n-----END RSA PRIVATE KEY-----",
    "username": "user@example.com",
    "is_sandbox": False,
}


def _create_connection(auth_client) -> str:
    return auth_client.post("/api/connections/", json=_CONN_PAYLOAD).json()["id"]


def _create_plan(auth_client, conn_id: str) -> str:
    return auth_client.post(
        "/api/load-plans/",
        json={"name": "Test Plan", "connection_id": conn_id},
    ).json()["id"]


def _create_query_step(auth_client, plan_id: str, soql: str) -> str:
    resp = auth_client.post(
        f"/api/load-plans/{plan_id}/steps",
        json={
            "sequence": 1,
            "object_name": "Account",
            "operation": "query",
            "soql": soql,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_dml_step(auth_client, plan_id: str, tmpdir: str) -> str:
    # Write a tiny CSV so discover_files finds something
    csv_path = os.path.join(tmpdir, "accounts.csv")
    with open(csv_path, "w") as f:
        f.write("Id,Name\n001,Acme\n")

    resp = auth_client.post(
        f"/api/load-plans/{plan_id}/steps",
        json={
            "sequence": 1,
            "object_name": "Account",
            "operation": "insert",
            "csv_file_pattern": csv_path,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestPreviewRouteQuery:
    def test_valid_soql_returns_explain_plan(self, auth_client) -> None:
        conn_id = _create_connection(auth_client)
        plan_id = _create_plan(auth_client, conn_id)
        step_id = _create_query_step(auth_client, plan_id, VALID_SOQL)

        explain_200 = make_200_response()

        with patch(
            "app.api.load_steps.get_access_token",
            new_callable=AsyncMock,
            return_value="mock_token",
        ), patch(
            "app.api.load_steps.explain_soql",
            new_callable=AsyncMock,
            return_value=SoqlExplainResult(
                valid=True,
                plan={"leadingOperation": "TableScan", "sobjectType": "Account"},
            ),
        ):
            resp = auth_client.post(f"/api/load-plans/{plan_id}/steps/{step_id}/preview")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["kind"] == "query"
        assert body["valid"] is True
        assert body["plan"]["leadingOperation"] == "TableScan"
        assert body["plan"]["sobjectType"] == "Account"
        assert body["matched_files"] == []
        assert body["total_rows"] == 0
        assert body.get("error") is None

    def test_invalid_soql_returns_error(self, auth_client) -> None:
        conn_id = _create_connection(auth_client)
        plan_id = _create_plan(auth_client, conn_id)
        step_id = _create_query_step(auth_client, plan_id, "SELECT Foo FROM")

        with patch(
            "app.api.load_steps.get_access_token",
            new_callable=AsyncMock,
            return_value="mock_token",
        ), patch(
            "app.api.load_steps.explain_soql",
            new_callable=AsyncMock,
            return_value=SoqlExplainResult(
                valid=False,
                error="MALFORMED_QUERY: unexpected end of query",
            ),
        ):
            resp = auth_client.post(f"/api/load-plans/{plan_id}/steps/{step_id}/preview")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["kind"] == "query"
        assert body["valid"] is False
        assert "MALFORMED_QUERY" in body["error"]
        assert body.get("plan") is None
        assert body["matched_files"] == []
        assert body["total_rows"] == 0

    def test_queryall_validated_same_way(self, auth_client) -> None:
        conn_id = _create_connection(auth_client)
        plan_id = _create_plan(auth_client, conn_id)
        resp = auth_client.post(
            f"/api/load-plans/{plan_id}/steps",
            json={
                "sequence": 1,
                "object_name": "Account",
                "operation": "queryAll",
                "soql": VALID_SOQL,
            },
        )
        assert resp.status_code == 201
        step_id = resp.json()["id"]

        with patch(
            "app.api.load_steps.get_access_token",
            new_callable=AsyncMock,
            return_value="mock_token",
        ), patch(
            "app.api.load_steps.explain_soql",
            new_callable=AsyncMock,
            return_value=SoqlExplainResult(
                valid=True,
                plan={"leadingOperation": "TableScan", "sobjectType": "Account"},
            ),
        ) as mock_explain:
            resp = auth_client.post(f"/api/load-plans/{plan_id}/steps/{step_id}/preview")

        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "query"
        assert body["valid"] is True
        # explain_soql was called (queryAll goes through the same path)
        mock_explain.assert_awaited_once()

    def test_auth_error_returns_502(self, auth_client) -> None:
        conn_id = _create_connection(auth_client)
        plan_id = _create_plan(auth_client, conn_id)
        step_id = _create_query_step(auth_client, plan_id, VALID_SOQL)

        from app.services.salesforce_auth import AuthError

        with patch(
            "app.api.load_steps.get_access_token",
            new_callable=AsyncMock,
            side_effect=AuthError("bad key"),
        ):
            resp = auth_client.post(f"/api/load-plans/{plan_id}/steps/{step_id}/preview")

        assert resp.status_code == 502

    def test_bulk_api_error_returns_502(self, auth_client) -> None:
        conn_id = _create_connection(auth_client)
        plan_id = _create_plan(auth_client, conn_id)
        step_id = _create_query_step(auth_client, plan_id, VALID_SOQL)

        with patch(
            "app.api.load_steps.get_access_token",
            new_callable=AsyncMock,
            return_value="mock_token",
        ), patch(
            "app.api.load_steps.explain_soql",
            new_callable=AsyncMock,
            side_effect=BulkAPIError("SF unavailable", status_code=503),
        ):
            resp = auth_client.post(f"/api/load-plans/{plan_id}/steps/{step_id}/preview")

        assert resp.status_code == 502


class TestValidateSoqlEndpoint:
    """Ad-hoc SOQL validation — does not require a persisted step."""

    def test_valid_soql(self, auth_client) -> None:
        conn_id = _create_connection(auth_client)
        plan_id = _create_plan(auth_client, conn_id)

        with patch(
            "app.api.load_steps.get_access_token",
            new_callable=AsyncMock,
            return_value="mock_token",
        ), patch(
            "app.api.load_steps.explain_soql",
            new_callable=AsyncMock,
            return_value=SoqlExplainResult(
                valid=True,
                plan={"leadingOperation": "TableScan", "sobjectType": "Account"},
            ),
        ):
            resp = auth_client.post(
                f"/api/load-plans/{plan_id}/validate-soql",
                json={"soql": VALID_SOQL},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["valid"] is True
        assert body["plan"]["sobjectType"] == "Account"
        assert body.get("error") is None

    def test_invalid_soql(self, auth_client) -> None:
        conn_id = _create_connection(auth_client)
        plan_id = _create_plan(auth_client, conn_id)

        with patch(
            "app.api.load_steps.get_access_token",
            new_callable=AsyncMock,
            return_value="mock_token",
        ), patch(
            "app.api.load_steps.explain_soql",
            new_callable=AsyncMock,
            return_value=SoqlExplainResult(
                valid=False,
                error="MALFORMED_QUERY: unexpected end of query",
            ),
        ):
            resp = auth_client.post(
                f"/api/load-plans/{plan_id}/validate-soql",
                json={"soql": "SELECT Foo FROM"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert "MALFORMED_QUERY" in body["error"]
        assert body.get("plan") is None

    def test_empty_soql_returns_invalid(self, auth_client) -> None:
        conn_id = _create_connection(auth_client)
        plan_id = _create_plan(auth_client, conn_id)

        resp = auth_client.post(
            f"/api/load-plans/{plan_id}/validate-soql",
            json={"soql": "   "},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert body["error"]

    def test_plan_not_found(self, auth_client) -> None:
        resp = auth_client.post(
            "/api/load-plans/does-not-exist/validate-soql",
            json={"soql": VALID_SOQL},
        )
        assert resp.status_code == 404


class TestPreviewRouteDmlRegression:
    def test_dml_step_returns_file_preview_envelope(self, auth_client, tmp_path) -> None:
        """DML steps must still return the original file-preview shape."""
        conn_id = _create_connection(auth_client)
        plan_id = _create_plan(auth_client, conn_id)

        csv_file = tmp_path / "accounts.csv"
        csv_file.write_text("Id,Name\n001,Acme\n002,Globex\n")

        step_resp = auth_client.post(
            f"/api/load-plans/{plan_id}/steps",
            json={
                "sequence": 1,
                "object_name": "Account",
                "operation": "insert",
                "csv_file_pattern": str(csv_file),
            },
        )
        assert step_resp.status_code == 201
        step_id = step_resp.json()["id"]

        resp = auth_client.post(f"/api/load-plans/{plan_id}/steps/{step_id}/preview")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["kind"] == "dml"
        assert isinstance(body["matched_files"], list)
        assert body["total_rows"] >= 0
        # Query-specific fields should be absent / null
        assert body.get("valid") is None
        assert body.get("plan") is None
        assert body.get("error") is None
