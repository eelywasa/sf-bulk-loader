"""Tests for tools/plans.py — load plan, step authoring, and validation tools.

Uses mocked BulkLoaderClient (MagicMock + AsyncMock) so no live server is needed.
Covers:
  - Happy path: correct endpoint + method called, formatted output returned.
  - Error mapping: 404/422/500 → structured McpHttpError text, no raw tracebacks.
  - output_connection_id on plan create/update.
  - input_from_step_id on step create/update (query-step output chaining).
  - Building a plan with ordered steps then reordering them.
  - validate_soql and preview_step surfacing backend results/errors faithfully.
  - Step-dependency / input-source validation errors passed through clearly.
"""

from __future__ import annotations

import copy
import pytest
from unittest.mock import AsyncMock, MagicMock, call

from sf_bulk_loader_mcp.client import McpHttpError
from sf_bulk_loader_mcp.tools.plans import (
    # Plans
    list_plans,
    format_list_plans,
    get_plan,
    format_plan,
    create_plan,
    format_create_plan,
    update_plan,
    format_update_plan,
    delete_plan,
    format_delete_plan,
    duplicate_plan,
    format_duplicate_plan,
    # Steps
    add_step,
    format_step,
    format_add_step,
    update_step,
    format_update_step,
    delete_step,
    format_delete_step,
    reorder_steps,
    format_reorder_steps,
    # Validation helpers
    validate_soql,
    format_validate_soql,
    preview_step,
    format_preview_step,
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


def _make_none_client() -> MagicMock:
    """Return a mock client whose DELETE returns a response with no JSON body (204)."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = None
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

PLAN_ID = "plan-aaa-111"
STEP_ID_1 = "step-bbb-001"
STEP_ID_2 = "step-bbb-002"

STEP_1 = {
    "id": STEP_ID_1,
    "load_plan_id": PLAN_ID,
    "sequence": 1,
    "object_name": "Account",
    "operation": "query",
    "soql": "SELECT Id, Name FROM Account",
    "csv_file_pattern": None,
    "external_id_field": None,
    "partition_size": None,
    "assignment_rule_id": None,
    "input_connection_id": None,
    "input_from_step_id": None,
    "name": "query-accounts",
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-01T00:00:00",
}

STEP_2 = {
    "id": STEP_ID_2,
    "load_plan_id": PLAN_ID,
    "sequence": 2,
    "object_name": "Account",
    "operation": "upsert",
    "soql": None,
    "csv_file_pattern": None,
    "external_id_field": "External_Id__c",
    "partition_size": 10000,
    "assignment_rule_id": None,
    "input_connection_id": None,
    "input_from_step_id": STEP_ID_1,
    "name": "upsert-accounts",
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-01T00:00:00",
}

PLAN = {
    "id": PLAN_ID,
    "connection_id": "conn-ccc-123",
    "name": "My Load Plan",
    "description": "Test plan",
    "output_connection_id": "ic-out-456",
    "abort_on_step_failure": True,
    "error_threshold_pct": 5.0,
    "max_parallel_jobs": 3,
    "consecutive_failure_threshold": 5,
    "load_steps": [STEP_1, STEP_2],
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-02T00:00:00",
}

PLAN_LIST_ITEM = {k: v for k, v in PLAN.items() if k != "load_steps"}


# ── Plans — happy path ─────────────────────────────────────────────────────────


class TestListPlans:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(self) -> None:
        client = _make_client([PLAN_LIST_ITEM])
        await list_plans(client)
        client.get.assert_called_once_with("/api/load-plans/")

    @pytest.mark.asyncio
    async def test_returns_payload(self) -> None:
        client = _make_client([PLAN_LIST_ITEM])
        result = await list_plans(client)
        assert result == [PLAN_LIST_ITEM]

    def test_format_non_empty(self) -> None:
        text = format_list_plans([PLAN])
        assert "My Load Plan" in text
        assert PLAN_ID in text
        assert "1" in text  # count

    def test_format_shows_output_connection(self) -> None:
        text = format_list_plans([PLAN])
        assert "ic-out-456" in text

    def test_format_empty(self) -> None:
        text = format_list_plans([])
        assert "No load plans" in text


class TestGetPlan:
    @pytest.mark.asyncio
    async def test_calls_correct_endpoint(self) -> None:
        client = _make_client(PLAN)
        await get_plan(client, PLAN_ID)
        client.get.assert_called_once_with(f"/api/load-plans/{PLAN_ID}")

    def test_format_includes_key_fields(self) -> None:
        text = format_plan(PLAN)
        assert "My Load Plan" in text
        assert PLAN_ID in text
        assert "conn-ccc-123" in text
        assert "ic-out-456" in text

    def test_format_shows_steps(self) -> None:
        text = format_plan(PLAN)
        assert "query-accounts" in text or "Account" in text
        assert STEP_ID_1 in text

    def test_format_shows_input_from_step_chain(self) -> None:
        """Steps with input_from_step_id should show the chaining reference."""
        text = format_plan(PLAN)
        assert STEP_ID_1 in text  # step 2 references step 1


class TestCreatePlan:
    CREATE_BODY = {
        "connection_id": "conn-ccc-123",
        "name": "New Plan",
        "output_connection_id": "ic-out-456",
    }

    @pytest.mark.asyncio
    async def test_calls_post_endpoint(self) -> None:
        client = _make_client(PLAN)
        await create_plan(client, self.CREATE_BODY)
        client.post.assert_called_once()
        assert client.post.call_args[0][0] == "/api/load-plans/"

    @pytest.mark.asyncio
    async def test_output_connection_id_in_body(self) -> None:
        """output_connection_id must be forwarded in the POST body for backend validation."""
        client = _make_client(PLAN)
        await create_plan(client, self.CREATE_BODY)
        body_sent = client.post.call_args[1]["json"]
        assert body_sent["output_connection_id"] == "ic-out-456"

    def test_format_create_includes_created_wording(self) -> None:
        text = format_create_plan(PLAN)
        assert "created" in text.lower()
        assert "My Load Plan" in text

    def test_format_create_shows_output_connection(self) -> None:
        text = format_create_plan(PLAN)
        assert "ic-out-456" in text


class TestUpdatePlan:
    @pytest.mark.asyncio
    async def test_calls_put_endpoint(self) -> None:
        client = _make_client(PLAN)
        await update_plan(client, PLAN_ID, {"name": "Renamed"})
        client.put.assert_called_once_with(
            f"/api/load-plans/{PLAN_ID}", json={"name": "Renamed"}
        )

    @pytest.mark.asyncio
    async def test_output_connection_id_in_update_body(self) -> None:
        """output_connection_id can be set on update and must reach the backend."""
        client = _make_client(PLAN)
        await update_plan(client, PLAN_ID, {"output_connection_id": "ic-new-789"})
        body_sent = client.put.call_args[1]["json"]
        assert body_sent["output_connection_id"] == "ic-new-789"

    def test_format_update_includes_updated_wording(self) -> None:
        text = format_update_plan(PLAN)
        assert "updated" in text.lower()


class TestDeletePlan:
    @pytest.mark.asyncio
    async def test_calls_delete_endpoint(self) -> None:
        client = _make_none_client()
        await delete_plan(client, PLAN_ID)
        client.delete.assert_called_once_with(f"/api/load-plans/{PLAN_ID}")

    def test_format_delete(self) -> None:
        text = format_delete_plan(PLAN_ID)
        assert "deleted" in text.lower()
        assert PLAN_ID in text


class TestDuplicatePlan:
    @pytest.mark.asyncio
    async def test_calls_duplicate_endpoint(self) -> None:
        client = _make_client(PLAN)
        await duplicate_plan(client, PLAN_ID)
        client.post.assert_called_once_with(f"/api/load-plans/{PLAN_ID}/duplicate")

    def test_format_duplicate_includes_wording(self) -> None:
        text = format_duplicate_plan(PLAN)
        assert "duplicated" in text.lower()
        assert "My Load Plan" in text


# ── Steps — happy path ─────────────────────────────────────────────────────────


class TestAddStep:
    ADD_BODY_DML = {
        "object_name": "Contact",
        "operation": "insert",
        "csv_file_pattern": "data/contacts/*.csv",
    }

    ADD_BODY_QUERY = {
        "object_name": "Account",
        "operation": "query",
        "soql": "SELECT Id, Name FROM Account",
    }

    ADD_BODY_CHAINED = {
        "object_name": "Account",
        "operation": "upsert",
        "external_id_field": "External_Id__c",
        "input_from_step_id": STEP_ID_1,
    }

    @pytest.mark.asyncio
    async def test_calls_post_endpoint(self) -> None:
        client = _make_client(STEP_1)
        await add_step(client, PLAN_ID, self.ADD_BODY_DML)
        client.post.assert_called_once()
        assert client.post.call_args[0][0] == f"/api/load-plans/{PLAN_ID}/steps"

    @pytest.mark.asyncio
    async def test_input_from_step_id_forwarded(self) -> None:
        """input_from_step_id must be forwarded in the POST body for backend validation."""
        client = _make_client(STEP_2)
        await add_step(client, PLAN_ID, self.ADD_BODY_CHAINED)
        body_sent = client.post.call_args[1]["json"]
        assert body_sent["input_from_step_id"] == STEP_ID_1

    def test_format_step_shows_chaining(self) -> None:
        """A step with input_from_step_id should surface it in the formatted output."""
        text = format_step(STEP_2)
        assert STEP_ID_1 in text

    def test_format_add_step_includes_added_wording(self) -> None:
        text = format_add_step(STEP_1)
        assert "added" in text.lower()

    def test_format_query_step_shows_soql(self) -> None:
        text = format_step(STEP_1)
        assert "SOQL" in text or "soql" in text.lower()


class TestUpdateStep:
    @pytest.mark.asyncio
    async def test_calls_put_endpoint(self) -> None:
        client = _make_client(STEP_1)
        await update_step(client, PLAN_ID, STEP_ID_1, {"soql": "SELECT Id FROM Account"})
        client.put.assert_called_once_with(
            f"/api/load-plans/{PLAN_ID}/steps/{STEP_ID_1}",
            json={"soql": "SELECT Id FROM Account"},
        )

    @pytest.mark.asyncio
    async def test_input_from_step_id_forwarded_on_update(self) -> None:
        """Setting input_from_step_id on update must reach the backend."""
        client = _make_client(STEP_2)
        await update_step(client, PLAN_ID, STEP_ID_2, {"input_from_step_id": STEP_ID_1})
        body_sent = client.put.call_args[1]["json"]
        assert body_sent["input_from_step_id"] == STEP_ID_1

    def test_format_update_step_includes_updated_wording(self) -> None:
        text = format_update_step(STEP_1)
        assert "updated" in text.lower()


class TestDeleteStep:
    @pytest.mark.asyncio
    async def test_calls_delete_endpoint(self) -> None:
        client = _make_none_client()
        await delete_step(client, PLAN_ID, STEP_ID_1)
        client.delete.assert_called_once_with(
            f"/api/load-plans/{PLAN_ID}/steps/{STEP_ID_1}"
        )

    def test_format_delete_step(self) -> None:
        text = format_delete_step(STEP_ID_1)
        assert "deleted" in text.lower()
        assert STEP_ID_1 in text


class TestReorderSteps:
    @pytest.mark.asyncio
    async def test_calls_reorder_endpoint_with_correct_body(self) -> None:
        """Reorder must POST to /steps/reorder with step_ids in the body."""
        client = _make_client([STEP_2, STEP_1])  # reversed order returned
        await reorder_steps(client, PLAN_ID, [STEP_ID_2, STEP_ID_1])
        client.post.assert_called_once_with(
            f"/api/load-plans/{PLAN_ID}/steps/reorder",
            json={"step_ids": [STEP_ID_2, STEP_ID_1]},
        )

    @pytest.mark.asyncio
    async def test_reorder_returns_updated_steps(self) -> None:
        """After reorder, the returned steps should reflect the new ordering."""
        reordered = [
            {**STEP_2, "sequence": 1},
            {**STEP_1, "sequence": 2},
        ]
        client = _make_client(reordered)
        result = await reorder_steps(client, PLAN_ID, [STEP_ID_2, STEP_ID_1])
        assert result[0]["id"] == STEP_ID_2
        assert result[1]["id"] == STEP_ID_1

    def test_format_reorder_steps_non_empty(self) -> None:
        text = format_reorder_steps([STEP_1, STEP_2])
        assert "reordered" in text.lower()
        assert STEP_ID_1 in text
        assert STEP_ID_2 in text

    def test_format_reorder_steps_empty(self) -> None:
        text = format_reorder_steps([])
        assert "No steps" in text


class TestBuildPlanWithStepsAndReorder:
    """Integration-style: simulate building a plan with ordered steps, then reordering them."""

    @pytest.mark.asyncio
    async def test_create_plan_then_add_steps_then_reorder(self) -> None:
        """
        Simulates the multi-step flow:
          1. Create plan (with output_connection_id).
          2. Add query step (step 1).
          3. Add chained DML step (step 2, input_from_step_id=step1).
          4. Reorder steps to swap sequences.
        Verifies endpoints + bodies at each stage.
        """
        plan_client = _make_client(PLAN)
        created = await create_plan(
            plan_client,
            {
                "connection_id": "conn-ccc-123",
                "name": "My Load Plan",
                "output_connection_id": "ic-out-456",
            },
        )
        assert created["id"] == PLAN_ID
        assert plan_client.post.call_args[0][0] == "/api/load-plans/"
        body = plan_client.post.call_args[1]["json"]
        assert body["output_connection_id"] == "ic-out-456"

        step1_client = _make_client(STEP_1)
        await add_step(
            step1_client,
            PLAN_ID,
            {"object_name": "Account", "operation": "query", "soql": "SELECT Id FROM Account"},
        )
        assert step1_client.post.call_args[0][0] == f"/api/load-plans/{PLAN_ID}/steps"

        step2_client = _make_client(STEP_2)
        await add_step(
            step2_client,
            PLAN_ID,
            {
                "object_name": "Account",
                "operation": "upsert",
                "external_id_field": "External_Id__c",
                "input_from_step_id": STEP_ID_1,
            },
        )
        body2 = step2_client.post.call_args[1]["json"]
        assert body2["input_from_step_id"] == STEP_ID_1

        reorder_client = _make_client([STEP_2, STEP_1])
        await reorder_steps(reorder_client, PLAN_ID, [STEP_ID_2, STEP_ID_1])
        reorder_client.post.assert_called_once_with(
            f"/api/load-plans/{PLAN_ID}/steps/reorder",
            json={"step_ids": [STEP_ID_2, STEP_ID_1]},
        )


# ── Validation helpers — happy path ───────────────────────────────────────────


class TestValidateSoql:
    @pytest.mark.asyncio
    async def test_calls_validate_soql_endpoint(self) -> None:
        client = _make_client({"valid": True, "plan": {"notes": []}, "error": None})
        await validate_soql(client, PLAN_ID, "SELECT Id FROM Account")
        client.post.assert_called_once_with(
            f"/api/load-plans/{PLAN_ID}/validate-soql",
            json={"soql": "SELECT Id FROM Account"},
        )

    def test_format_valid(self) -> None:
        payload = {"valid": True, "plan": {"notes": []}, "error": None}
        text = format_validate_soql(payload)
        assert "VALID" in text
        assert "INVALID" not in text

    def test_format_invalid(self) -> None:
        payload = {"valid": False, "plan": None, "error": "Unknown field: Foo__c"}
        text = format_validate_soql(payload)
        assert "INVALID" in text
        assert "Unknown field: Foo__c" in text

    @pytest.mark.asyncio
    async def test_backend_error_surfaced_faithfully(self) -> None:
        """A 502 from validate-soql (e.g. Salesforce auth failure) must reach the caller clearly."""
        client = _make_error_client(502, "Backend server error (HTTP 502)", "Could not authenticate with Salesforce")
        with pytest.raises(McpHttpError) as exc_info:
            await validate_soql(client, PLAN_ID, "SELECT Id FROM Account")
        text = exc_info.value.to_tool_error_text()
        assert "502" in text
        assert "Traceback" not in text


class TestPreviewStep:
    @pytest.mark.asyncio
    async def test_calls_preview_endpoint(self) -> None:
        client = _make_client({"kind": "dml", "pattern": "*.csv", "matched_files": [], "total_rows": 0})
        await preview_step(client, PLAN_ID, STEP_ID_1)
        client.post.assert_called_once_with(
            f"/api/load-plans/{PLAN_ID}/steps/{STEP_ID_1}/preview"
        )

    def test_format_dml_with_files(self) -> None:
        payload = {
            "kind": "dml",
            "pattern": "data/accounts/*.csv",
            "matched_files": [
                {"filename": "accounts_1.csv", "row_count": 500},
                {"filename": "accounts_2.csv", "row_count": 300},
            ],
            "total_rows": 800,
        }
        text = format_preview_step(payload)
        assert "dml" in text
        assert "800" in text
        assert "accounts_1.csv" in text

    def test_format_query_valid(self) -> None:
        payload = {
            "kind": "query",
            "valid": True,
            "plan": {"notes": []},
            "error": None,
            "matched_files": [],
            "total_rows": 0,
        }
        text = format_preview_step(payload)
        assert "query" in text
        assert "VALID" in text

    def test_format_query_invalid(self) -> None:
        payload = {
            "kind": "query",
            "valid": False,
            "plan": None,
            "error": "Unknown field",
            "matched_files": [],
            "total_rows": 0,
        }
        text = format_preview_step(payload)
        assert "INVALID" in text
        assert "Unknown field" in text

    def test_format_chained_step_returns_note(self) -> None:
        payload = {
            "kind": "dml",
            "pattern": None,
            "matched_files": [],
            "total_rows": 0,
            "note": "Input resolved at run time from upstream step: query-accounts",
        }
        text = format_preview_step(payload)
        assert "run time" in text or "upstream" in text

    @pytest.mark.asyncio
    async def test_backend_error_surfaced_faithfully(self) -> None:
        """A 404 from preview (e.g. plan not found) must reach the caller clearly."""
        client = _make_error_client(404, "Resource not found", "Load plan not found")
        with pytest.raises(McpHttpError) as exc_info:
            await preview_step(client, PLAN_ID, STEP_ID_1)
        text = exc_info.value.to_tool_error_text()
        assert "404" in text
        assert "Traceback" not in text


# ── Error mapping ──────────────────────────────────────────────────────────────


class TestPlanErrors:
    @pytest.mark.asyncio
    async def test_list_plans_404(self) -> None:
        client = _make_error_client(404, "Resource not found")
        with pytest.raises(McpHttpError) as exc_info:
            await list_plans(client)
        assert exc_info.value.status_code == 404
        assert "Traceback" not in exc_info.value.to_tool_error_text()
        assert 'File "' not in exc_info.value.to_tool_error_text()

    @pytest.mark.asyncio
    async def test_create_plan_422_output_connection_wrong_direction(self) -> None:
        """Backend returns 422 when output_connection_id has wrong direction; must pass through clearly."""
        detail = (
            "Storage connection 'ic-in-123' has direction 'in' "
            "but must be 'out' or 'both' to be used as an output destination"
        )
        client = _make_error_client(422, "Unprocessable request — validation error", detail)
        with pytest.raises(McpHttpError) as exc_info:
            await create_plan(client, {"connection_id": "x", "name": "P", "output_connection_id": "ic-in-123"})
        text = exc_info.value.to_tool_error_text()
        assert "422" in text
        assert "Traceback" not in text

    @pytest.mark.asyncio
    async def test_get_plan_404(self) -> None:
        client = _make_error_client(404, "Resource not found", "Load plan not found")
        with pytest.raises(McpHttpError) as exc_info:
            await get_plan(client, "missing-id")
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Load plan not found"

    @pytest.mark.asyncio
    async def test_delete_plan_500(self) -> None:
        client = _make_error_client(500, "Backend server error (HTTP 500)")
        with pytest.raises(McpHttpError) as exc_info:
            await delete_plan(client, PLAN_ID)
        text = exc_info.value.to_tool_error_text()
        assert "500" in text
        assert "Traceback" not in text


class TestStepErrors:
    @pytest.mark.asyncio
    async def test_add_step_422_input_from_step_id_conflict(self) -> None:
        """Backend returns 422 when input_from_step_id + csv_file_pattern are both set."""
        detail = (
            "'input_from_step_id' and 'csv_file_pattern' cannot both be set — "
            "a step's input source is either an upstream step or a file pattern, not both"
        )
        client = _make_error_client(422, "Unprocessable request — validation error", detail)
        with pytest.raises(McpHttpError) as exc_info:
            await add_step(
                client,
                PLAN_ID,
                {
                    "object_name": "Account",
                    "operation": "upsert",
                    "csv_file_pattern": "data/*.csv",
                    "input_from_step_id": STEP_ID_1,
                },
            )
        text = exc_info.value.to_tool_error_text()
        assert "422" in text
        assert "Traceback" not in text

    @pytest.mark.asyncio
    async def test_add_step_422_invalid_input_from_step_id_sequence(self) -> None:
        """Backend returns 422 if input_from_step_id references a step with higher sequence."""
        detail = "Step input reference must have a lower sequence number"
        client = _make_error_client(422, "Unprocessable request — validation error", detail)
        with pytest.raises(McpHttpError) as exc_info:
            await add_step(
                client,
                PLAN_ID,
                {
                    "object_name": "Account",
                    "operation": "upsert",
                    "input_from_step_id": STEP_ID_2,
                    "sequence": 1,
                },
            )
        text = exc_info.value.to_tool_error_text()
        assert "422" in text

    @pytest.mark.asyncio
    async def test_delete_step_422_dependency_error_passed_through(self) -> None:
        """Backend returns 422 when a downstream step depends on the step being deleted."""
        detail = (
            "Cannot delete this step — it is the input source for: upsert-accounts. "
            "Clear or repoint the downstream step's input source first."
        )
        client = _make_error_client(422, "Unprocessable request — validation error", detail)
        with pytest.raises(McpHttpError) as exc_info:
            await delete_step(client, PLAN_ID, STEP_ID_1)
        text = exc_info.value.to_tool_error_text()
        assert "422" in text
        assert "Traceback" not in text

    @pytest.mark.asyncio
    async def test_reorder_steps_422(self) -> None:
        client = _make_error_client(422, "Unprocessable request — validation error", "Step IDs do not match plan steps")
        with pytest.raises(McpHttpError) as exc_info:
            await reorder_steps(client, PLAN_ID, ["wrong-id"])
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_update_step_404(self) -> None:
        client = _make_error_client(404, "Resource not found", "Load step not found")
        with pytest.raises(McpHttpError) as exc_info:
            await update_step(client, PLAN_ID, "missing-step", {"soql": "SELECT Id FROM Account"})
        assert exc_info.value.status_code == 404
