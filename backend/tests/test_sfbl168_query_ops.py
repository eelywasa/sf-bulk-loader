"""Tests for SFBL-168: query/queryAll operation support.

Covers:
- Operation enum round-tripping
- Schema validator: soql required iff query/queryAll, forbidden for DML
- Schema validator: csv_file_pattern required iff DML, forbidden for query ops
- partition_size accepted (not rejected) for query ops
- Preview envelope for query ops returns kind="query" without file discovery
"""

import os
import tempfile
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.models.load_step import Operation, QUERY_OPERATIONS, DML_OPERATIONS
from app.schemas.load_step import LoadStepCreate, LoadStepUpdate


# ── Fixtures ──────────────────────────────────────────────────────────────────

_CONN = {
    "name": "Test Org",
    "instance_url": "https://myorg.my.salesforce.com",
    "login_url": "https://login.salesforce.com",
    "client_id": "cid",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----",
    "username": "u@example.com",
    "is_sandbox": False,
}


def _conn_id(auth_client) -> str:
    return auth_client.post("/api/connections/", json=_CONN).json()["id"]


def _plan_id(auth_client, conn_id: str) -> str:
    return auth_client.post(
        "/api/load-plans/",
        json={"name": "Plan", "connection_id": conn_id},
    ).json()["id"]


# ── Enum round-trip ───────────────────────────────────────────────────────────


def test_operation_enum_contains_query():
    assert Operation.query == "query"
    assert Operation.queryAll == "queryAll"


def test_operation_enum_query_in_query_operations_set():
    assert Operation.query in QUERY_OPERATIONS
    assert Operation.queryAll in QUERY_OPERATIONS


def test_operation_enum_dml_not_in_query_operations_set():
    for op in DML_OPERATIONS:
        assert op not in QUERY_OPERATIONS


def test_operation_enum_dml_contains_four_values():
    assert len(DML_OPERATIONS) == 4
    for op in (Operation.insert, Operation.update, Operation.upsert, Operation.delete):
        assert op in DML_OPERATIONS


def test_operation_enum_total_values():
    all_ops = list(Operation)
    assert len(all_ops) == 6


# ── Schema validators — LoadStepCreate ───────────────────────────────────────


def test_create_query_step_valid():
    step = LoadStepCreate(
        object_name="Account",
        operation=Operation.query,
        soql="SELECT Id, Name FROM Account",
    )
    assert step.soql == "SELECT Id, Name FROM Account"
    assert step.csv_file_pattern is None


def test_create_query_all_step_valid():
    step = LoadStepCreate(
        object_name="Account",
        operation=Operation.queryAll,
        soql="SELECT Id, Name FROM Account",
    )
    assert step.soql == "SELECT Id, Name FROM Account"


def test_create_query_step_missing_soql_raises():
    with pytest.raises(ValidationError) as exc_info:
        LoadStepCreate(
            object_name="Account",
            operation=Operation.query,
            csv_file_pattern=None,
            soql=None,
        )
    errors = exc_info.value.errors()
    assert any("soql" in str(e) for e in errors)


def test_create_query_step_with_csv_pattern_raises():
    with pytest.raises(ValidationError) as exc_info:
        LoadStepCreate(
            object_name="Account",
            operation=Operation.query,
            soql="SELECT Id FROM Account",
            csv_file_pattern="accounts_*.csv",
        )
    errors = exc_info.value.errors()
    assert any("csv_file_pattern" in str(e) for e in errors)


def test_create_dml_step_valid():
    step = LoadStepCreate(
        object_name="Account",
        operation=Operation.insert,
        csv_file_pattern="accounts_*.csv",
    )
    assert step.csv_file_pattern == "accounts_*.csv"
    assert step.soql is None


def test_create_dml_step_missing_csv_pattern_raises():
    with pytest.raises(ValidationError) as exc_info:
        LoadStepCreate(
            object_name="Account",
            operation=Operation.insert,
            csv_file_pattern=None,
        )
    errors = exc_info.value.errors()
    assert any("csv_file_pattern" in str(e) for e in errors)


def test_create_dml_step_with_soql_raises():
    with pytest.raises(ValidationError) as exc_info:
        LoadStepCreate(
            object_name="Account",
            operation=Operation.insert,
            csv_file_pattern="accounts_*.csv",
            soql="SELECT Id FROM Account",
        )
    errors = exc_info.value.errors()
    assert any("soql" in str(e) for e in errors)


def test_create_query_step_partition_size_accepted():
    """partition_size should be accepted (not rejected) for query ops."""
    step = LoadStepCreate(
        object_name="Account",
        operation=Operation.query,
        soql="SELECT Id FROM Account",
        partition_size=5000,
    )
    assert step.partition_size == 5000


# ── Schema validators — LoadStepUpdate ───────────────────────────────────────


def test_update_no_operation_skips_cross_validation():
    """Partial update omitting operation should not trigger cross-field validation."""
    step = LoadStepUpdate(object_name="Contact")
    assert step.object_name == "Contact"


def test_update_query_op_with_soql_valid():
    step = LoadStepUpdate(
        operation=Operation.query,
        soql="SELECT Id FROM Lead",
    )
    assert step.soql == "SELECT Id FROM Lead"


def test_update_query_op_missing_soql_raises():
    with pytest.raises(ValidationError) as exc_info:
        LoadStepUpdate(
            operation=Operation.query,
            soql=None,
        )
    errors = exc_info.value.errors()
    assert any("soql" in str(e) for e in errors)


def test_update_dml_op_with_csv_valid():
    step = LoadStepUpdate(
        operation=Operation.upsert,
        csv_file_pattern="contacts_*.csv",
    )
    assert step.csv_file_pattern == "contacts_*.csv"


def test_update_dml_op_missing_csv_pattern_raises():
    with pytest.raises(ValidationError) as exc_info:
        LoadStepUpdate(
            operation=Operation.upsert,
            csv_file_pattern=None,
        )
    errors = exc_info.value.errors()
    assert any("csv_file_pattern" in str(e) for e in errors)


# ── API integration — creating query steps ────────────────────────────────────


def test_api_create_query_step_returns_201(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    payload = {
        "object_name": "Account",
        "operation": "query",
        "soql": "SELECT Id, Name FROM Account",
    }
    resp = auth_client.post(f"/api/load-plans/{pid}/steps", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["operation"] == "query"
    assert body["soql"] == "SELECT Id, Name FROM Account"
    assert body["csv_file_pattern"] is None


def test_api_create_query_all_step_returns_201(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    payload = {
        "object_name": "Account",
        "operation": "queryAll",
        "soql": "SELECT Id, Name FROM Account",
    }
    resp = auth_client.post(f"/api/load-plans/{pid}/steps", json=payload)
    assert resp.status_code == 201
    assert resp.json()["operation"] == "queryAll"


def test_api_create_query_step_with_csv_pattern_returns_422(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    payload = {
        "object_name": "Account",
        "operation": "query",
        "soql": "SELECT Id FROM Account",
        "csv_file_pattern": "accounts_*.csv",
    }
    resp = auth_client.post(f"/api/load-plans/{pid}/steps", json=payload)
    assert resp.status_code == 422


def test_api_create_query_step_missing_soql_returns_422(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    payload = {
        "object_name": "Account",
        "operation": "query",
    }
    resp = auth_client.post(f"/api/load-plans/{pid}/steps", json=payload)
    assert resp.status_code == 422


def test_api_create_dml_step_without_csv_pattern_returns_422(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    payload = {
        "object_name": "Account",
        "operation": "insert",
    }
    resp = auth_client.post(f"/api/load-plans/{pid}/steps", json=payload)
    assert resp.status_code == 422


def test_api_create_dml_step_with_soql_returns_422(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    payload = {
        "object_name": "Account",
        "operation": "insert",
        "csv_file_pattern": "accounts_*.csv",
        "soql": "SELECT Id FROM Account",
    }
    resp = auth_client.post(f"/api/load-plans/{pid}/steps", json=payload)
    assert resp.status_code == 422


# ── Preview envelope — query step ────────────────────────────────────────────


def test_preview_query_step_returns_query_envelope(auth_client):
    """Preview for a query step must return kind=query with explain result (SFBL-175)."""
    from unittest.mock import AsyncMock
    from app.services.salesforce_query_validation import SoqlExplainResult

    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_payload = {
        "object_name": "Account",
        "operation": "query",
        "soql": "SELECT Id, Name FROM Account",
    }
    step_id = auth_client.post(f"/api/load-plans/{pid}/steps", json=step_payload).json()["id"]

    with patch("app.api.load_steps.get_access_token", new_callable=AsyncMock, return_value="tok"), \
         patch("app.api.load_steps.explain_soql", new_callable=AsyncMock,
               return_value=SoqlExplainResult(valid=True, plan={"leadingOperation": "TableScan", "sobjectType": "Account"})):
        resp = auth_client.post(f"/api/load-plans/{pid}/steps/{step_id}/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "query"
    assert body["matched_files"] == []
    assert body["total_rows"] == 0
    assert body["valid"] is True


def test_preview_query_all_step_returns_query_envelope(auth_client):
    """Preview for a queryAll step must also return kind=query (SFBL-175)."""
    from unittest.mock import AsyncMock
    from app.services.salesforce_query_validation import SoqlExplainResult

    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_payload = {
        "object_name": "Account",
        "operation": "queryAll",
        "soql": "SELECT Id, Name FROM Account",
    }
    step_id = auth_client.post(f"/api/load-plans/{pid}/steps", json=step_payload).json()["id"]

    with patch("app.api.load_steps.get_access_token", new_callable=AsyncMock, return_value="tok"), \
         patch("app.api.load_steps.explain_soql", new_callable=AsyncMock,
               return_value=SoqlExplainResult(valid=True, plan={})):
        resp = auth_client.post(f"/api/load-plans/{pid}/steps/{step_id}/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "query"
    assert body["total_rows"] == 0


def test_preview_dml_step_still_works_after_changes(auth_client):
    """Ensure DML step preview still works correctly (regression guard)."""
    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_payload = {
        "object_name": "Account",
        "operation": "insert",
        "csv_file_pattern": "accounts_*.csv",
    }
    step_id = auth_client.post(f"/api/load-plans/{pid}/steps", json=step_payload).json()["id"]

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "accounts_001.csv")
        with open(path, "w") as f:
            f.write("Name,Email\nAlice,a@b.com\nBob,b@b.com\n")

        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.post(f"/api/load-plans/{pid}/steps/{step_id}/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "dml"
    assert body["total_rows"] == 2
    assert len(body["matched_files"]) == 1
