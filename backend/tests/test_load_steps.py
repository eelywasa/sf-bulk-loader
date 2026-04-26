"""Tests for the /api/load-plans/{plan_id}/steps endpoints."""

import io
import os
import tempfile
from unittest.mock import AsyncMock, patch

from botocore.exceptions import ClientError
from app.services.input_storage import InputConnectionNotFoundError

_CONN = {
    "name": "Test Org",
    "instance_url": "https://myorg.my.salesforce.com",
    "login_url": "https://login.salesforce.com",
    "client_id": "cid",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----",
    "username": "u@example.com",
    "is_sandbox": False,
}

_STEP = {
    "sequence": 1,
    "object_name": "Account",
    "operation": "insert",
    "csv_file_pattern": "accounts_*.csv",
    "partition_size": 5000,
}


def _conn_id(auth_client) -> str:
    return auth_client.post("/api/connections/", json=_CONN).json()["id"]


def _plan_id(auth_client, conn_id: str) -> str:
    return auth_client.post(
        "/api/load-plans/",
        json={"name": "Plan", "connection_id": conn_id},
    ).json()["id"]


def _add_step(auth_client, plan_id: str, overrides=None) -> dict:
    payload = {**_STEP, **(overrides or {})}
    return auth_client.post(f"/api/load-plans/{plan_id}/steps", json=payload).json()


class _FakeS3Body:
    def __init__(self, data: bytes) -> None:
        self._io = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._io.read() if n == -1 else self._io.read(n)


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **_kwargs):
        return list(self._pages)


class _FakeS3Client:
    def __init__(self, *, object_map=None, pages=None, get_error=None) -> None:
        self._object_map = object_map or {}
        self._pages = pages or []
        self._get_error = get_error

    def get_paginator(self, _name):
        return _FakePaginator(self._pages)

    def get_object(self, *, Bucket, Key):
        if self._get_error is not None:
            raise self._get_error
        if Key not in self._object_map:
            error = {"Error": {"Code": "NoSuchKey", "Message": f"{Key} missing"}}
            raise ClientError(error, "GetObject")
        return {"Body": _FakeS3Body(self._object_map[Key])}


class _DiscoveryErrorPaginator:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def paginate(self, **_kwargs):
        raise self._error


class _DiscoveryErrorClient(_FakeS3Client):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def get_paginator(self, _name):
        return _DiscoveryErrorPaginator(self._error)


def _create_input_connection(auth_client) -> str:
    payload = {
        "name": "My S3 Bucket",
        "provider": "s3",
        "bucket": "my-bucket",
        "root_prefix": "data/",
        "region": "eu-west-2",
        "access_key_id": "AKIAIOSFODNN7EXAMPLE",
        "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    }
    resp = auth_client.post("/api/input-connections/", json=payload)
    assert resp.status_code == 201
    return resp.json()["id"]


# ── Add step ───────────────────────────────────────────────────────────────────


def test_add_step_returns_201(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    resp = auth_client.post(f"/api/load-plans/{pid}/steps", json=_STEP)
    assert resp.status_code == 201
    body = resp.json()
    assert body["object_name"] == "Account"
    assert body["operation"] == "insert"
    assert body["load_plan_id"] == pid


def test_add_step_plan_not_found_returns_404(auth_client):
    resp = auth_client.post("/api/load-plans/bad-plan/steps", json=_STEP)
    assert resp.status_code == 404


def test_add_step_upsert_accepts_external_id(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    step = {**_STEP, "operation": "upsert", "external_id_field": "ExternalId__c"}
    resp = auth_client.post(f"/api/load-plans/{pid}/steps", json=step)
    assert resp.status_code == 201
    assert resp.json()["external_id_field"] == "ExternalId__c"


def test_add_step_invalid_operation_returns_422(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    bad = {**_STEP, "operation": "merge"}
    resp = auth_client.post(f"/api/load-plans/{pid}/steps", json=bad)
    assert resp.status_code == 422


def test_add_step_with_input_connection_id_stores_and_returns_it(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    input_connection_id = _create_input_connection(auth_client)

    resp = auth_client.post(
        f"/api/load-plans/{pid}/steps",
        json={**_STEP, "input_connection_id": input_connection_id},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["input_connection_id"] == input_connection_id


# ── Auto-sequence ──────────────────────────────────────────────────────────────


def test_add_step_without_sequence_assigns_1(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    payload = {k: v for k, v in _STEP.items() if k != "sequence"}
    resp = auth_client.post(f"/api/load-plans/{pid}/steps", json=payload)
    assert resp.status_code == 201
    assert resp.json()["sequence"] == 1


def test_add_step_without_sequence_appends_to_end(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    payload = {k: v for k, v in _STEP.items() if k != "sequence"}
    s1 = auth_client.post(f"/api/load-plans/{pid}/steps", json=payload).json()
    s2 = auth_client.post(f"/api/load-plans/{pid}/steps", json=payload).json()
    s3 = auth_client.post(f"/api/load-plans/{pid}/steps", json=payload).json()
    assert s1["sequence"] == 1
    assert s2["sequence"] == 2
    assert s3["sequence"] == 3


# ── Update step ────────────────────────────────────────────────────────────────


def test_update_step_returns_updated_fields(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_id = _add_step(auth_client, pid)["id"]
    resp = auth_client.put(
        f"/api/load-plans/{pid}/steps/{step_id}",
        json={"object_name": "Contact", "partition_size": 1000},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object_name"] == "Contact"
    assert body["partition_size"] == 1000


def test_update_step_rejects_clearing_csv_pattern_on_dml(auth_client):
    """Partial update that would leave a DML step without csv_file_pattern → 422."""
    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_id = _add_step(auth_client, pid)["id"]
    resp = auth_client.put(
        f"/api/load-plans/{pid}/steps/{step_id}",
        json={"csv_file_pattern": None},
    )
    assert resp.status_code == 422
    assert "csv_file_pattern" in resp.json()["detail"]


def test_update_step_rejects_adding_soql_to_dml(auth_client):
    """Setting soql on a DML step without switching operation → 422."""
    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_id = _add_step(auth_client, pid)["id"]
    resp = auth_client.put(
        f"/api/load-plans/{pid}/steps/{step_id}",
        json={"soql": "SELECT Id FROM Account"},
    )
    assert resp.status_code == 422
    assert "soql" in resp.json()["detail"]


def test_update_step_operation_switch_to_query_requires_soql(auth_client):
    """Switching a DML step to query without providing soql → 422."""
    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_id = _add_step(auth_client, pid)["id"]
    resp = auth_client.put(
        f"/api/load-plans/{pid}/steps/{step_id}",
        json={"operation": "query", "csv_file_pattern": None},
    )
    assert resp.status_code == 422


def test_update_step_operation_switch_to_query_with_soql_succeeds(auth_client):
    """Atomic switch DML→query providing soql and clearing csv_file_pattern → 200."""
    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_id = _add_step(auth_client, pid)["id"]
    resp = auth_client.put(
        f"/api/load-plans/{pid}/steps/{step_id}",
        json={
            "operation": "query",
            "soql": "SELECT Id FROM Account",
            "csv_file_pattern": None,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["operation"] == "query"
    assert body["soql"] == "SELECT Id FROM Account"
    assert body["csv_file_pattern"] is None


def test_update_step_not_found_returns_404(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    resp = auth_client.put(f"/api/load-plans/{pid}/steps/bad-id", json={"sequence": 2})
    assert resp.status_code == 404


def test_update_step_wrong_plan_returns_404(auth_client):
    cid = _conn_id(auth_client)
    pid1 = _plan_id(auth_client, cid)
    pid2 = _plan_id(auth_client, cid)
    step_id = _add_step(auth_client, pid1)["id"]
    # Try updating the step under the wrong plan
    resp = auth_client.put(f"/api/load-plans/{pid2}/steps/{step_id}", json={"sequence": 2})
    assert resp.status_code == 404


def test_update_step_sets_input_connection_id(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    input_connection_id = _create_input_connection(auth_client)
    step_id = _add_step(auth_client, pid)["id"]

    resp = auth_client.put(
        f"/api/load-plans/{pid}/steps/{step_id}",
        json={"input_connection_id": input_connection_id},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["input_connection_id"] == input_connection_id


def test_update_step_clears_input_connection_id(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    input_connection_id = _create_input_connection(auth_client)
    step_id = _add_step(
        auth_client,
        pid,
        {"input_connection_id": input_connection_id},
    )["id"]

    resp = auth_client.put(
        f"/api/load-plans/{pid}/steps/{step_id}",
        json={"input_connection_id": None},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["input_connection_id"] is None


def test_create_step_accepts_local_output_sentinel(auth_client):
    """SFBL-178: input_connection_id='local-output' bypasses connection FK lookup."""
    pid = _plan_id(auth_client, _conn_id(auth_client))
    resp = auth_client.post(
        f"/api/load-plans/{pid}/steps",
        json={**_STEP, "input_connection_id": "local-output"},
    )
    assert resp.status_code == 201
    assert resp.json()["input_connection_id"] == "local-output"


def test_update_step_accepts_local_output_sentinel(auth_client):
    """SFBL-178: step can be switched to the local-output sentinel via PUT."""
    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_id = _add_step(auth_client, pid)["id"]
    resp = auth_client.put(
        f"/api/load-plans/{pid}/steps/{step_id}",
        json={"input_connection_id": "local-output"},
    )
    assert resp.status_code == 200
    assert resp.json()["input_connection_id"] == "local-output"


# ── Delete step ────────────────────────────────────────────────────────────────


def test_delete_step_returns_204(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_id = _add_step(auth_client, pid)["id"]
    assert auth_client.delete(f"/api/load-plans/{pid}/steps/{step_id}").status_code == 204


def test_delete_step_not_found_returns_404(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    assert auth_client.delete(f"/api/load-plans/{pid}/steps/bad-id").status_code == 404


# ── Reorder steps ──────────────────────────────────────────────────────────────


def test_reorder_steps_changes_sequence(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    s1 = _add_step(auth_client, pid, {"sequence": 1, "object_name": "Account"})["id"]
    s2 = _add_step(auth_client, pid, {"sequence": 2, "object_name": "Contact"})["id"]
    s3 = _add_step(auth_client, pid, {"sequence": 3, "object_name": "Lead"})["id"]

    # Reverse the order
    resp = auth_client.post(
        f"/api/load-plans/{pid}/steps/reorder",
        json={"step_ids": [s3, s2, s1]},
    )
    assert resp.status_code == 200
    steps = resp.json()
    assert [s["id"] for s in steps] == [s3, s2, s1]
    assert [s["sequence"] for s in steps] == [1, 2, 3]


def test_reorder_steps_missing_step_returns_400(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    s1 = _add_step(auth_client, pid, {"sequence": 1})["id"]
    _add_step(auth_client, pid, {"sequence": 2})

    # Provide only one step ID instead of two
    resp = auth_client.post(
        f"/api/load-plans/{pid}/steps/reorder",
        json={"step_ids": [s1]},
    )
    assert resp.status_code == 400


def test_reorder_steps_plan_not_found_returns_404(auth_client):
    resp = auth_client.post("/api/load-plans/bad/steps/reorder", json={"step_ids": []})
    assert resp.status_code == 404


# ── Preview step ───────────────────────────────────────────────────────────────


def test_preview_step_returns_matched_files(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_id = _add_step(auth_client, pid, {"csv_file_pattern": "*.csv"})["id"]

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create two small CSV files
        for i in range(2):
            path = os.path.join(tmpdir, f"file_{i}.csv")
            with open(path, "w") as f:
                f.write("Name,Email\nAlice,a@b.com\nBob,b@b.com\n")

        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.post(f"/api/load-plans/{pid}/steps/{step_id}/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_rows"] == 4  # 2 rows * 2 files
    assert len(body["matched_files"]) == 2


def test_preview_step_no_files_returns_empty(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_id = _add_step(auth_client, pid, {"csv_file_pattern": "nonexistent_*.csv"})["id"]

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.post(f"/api/load-plans/{pid}/steps/{step_id}/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_rows"] == 0
    assert body["matched_files"] == []


def test_preview_step_not_found_returns_404(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    resp = auth_client.post(f"/api/load-plans/{pid}/steps/bad-step/preview")
    assert resp.status_code == 404


def test_preview_step_traversal_pattern_returns_400(auth_client):
    """A step pattern containing '..' must be rejected with 400."""
    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_id = _add_step(auth_client, pid, {"csv_file_pattern": "../../etc/passwd"})["id"]

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.post(f"/api/load-plans/{pid}/steps/{step_id}/preview")

    assert resp.status_code == 400


def test_preview_step_cp1252_file_returns_correct_row_count(auth_client):
    """Row count for a cp1252-encoded file must be reported correctly."""
    import csv as _csv

    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_id = _add_step(auth_client, pid, {"csv_file_pattern": "cp1252_data.csv"})["id"]

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "cp1252_data.csv")
        # Write header + 3 rows with a cp1252-specific byte in values
        with open(csv_path, "wb") as f:
            f.write(b"Name,Value\nCaf\x80,1\nCaf\x80,2\nCaf\x80,3\n")

        with patch("app.services.input_storage.settings.input_dir", tmpdir):
            resp = auth_client.post(f"/api/load-plans/{pid}/steps/{step_id}/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_rows"] == 3


def test_preview_step_remote_source_returns_matched_files(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    input_connection_id = _create_input_connection(auth_client)
    step_id = _add_step(
        auth_client,
        pid,
        {"csv_file_pattern": "accounts/*.csv", "input_connection_id": input_connection_id},
    )["id"]

    client = _FakeS3Client(
        object_map={
            "data/accounts/file_1.csv": b"Name\nAlice\nBob\n",
            "data/accounts/file_2.csv": b"Name\nCarol\n",
        },
        pages=[
            {
                "Contents": [
                    {"Key": "data/accounts/file_1.csv"},
                    {"Key": "data/accounts/file_2.csv"},
                ]
            }
        ],
    )

    with patch("app.services.input_storage.boto3.client", return_value=client):
        resp = auth_client.post(f"/api/load-plans/{pid}/steps/{step_id}/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_rows"] == 3
    assert body["matched_files"] == [
        {"filename": "file_1.csv", "row_count": 2},
        {"filename": "file_2.csv", "row_count": 1},
    ]


def test_preview_step_remote_missing_input_connection_returns_404(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    input_connection_id = _create_input_connection(auth_client)
    step_id = _add_step(
        auth_client,
        pid,
        {"csv_file_pattern": "*.csv", "input_connection_id": input_connection_id},
    )["id"]

    with patch(
        "app.api.load_steps.get_storage",
        new=AsyncMock(side_effect=InputConnectionNotFoundError("Input connection not found: missing")),
    ):
        resp = auth_client.post(f"/api/load-plans/{pid}/steps/{step_id}/preview")

    assert resp.status_code == 404


def test_preview_step_remote_discovery_error_returns_400(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    input_connection_id = _create_input_connection(auth_client)
    step_id = _add_step(
        auth_client,
        pid,
        {"csv_file_pattern": "**/*.csv", "input_connection_id": input_connection_id},
    )["id"]

    error = ClientError({"Error": {"Code": "AccessDenied", "Message": "Denied"}}, "ListObjectsV2")
    client = _DiscoveryErrorClient(error)

    with patch("app.services.input_storage.boto3.client", return_value=client):
        resp = auth_client.post(f"/api/load-plans/{pid}/steps/{step_id}/preview")

    assert resp.status_code == 400


def test_preview_step_remote_file_read_error_returns_zero_row_count(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    input_connection_id = _create_input_connection(auth_client)
    step_id = _add_step(
        auth_client,
        pid,
        {"csv_file_pattern": "accounts/*.csv", "input_connection_id": input_connection_id},
    )["id"]

    client = _FakeS3Client(
        object_map={"data/accounts/good.csv": b"Name\nAlice\n"},
        pages=[
            {
                "Contents": [
                    {"Key": "data/accounts/good.csv"},
                    {"Key": "data/accounts/missing.csv"},
                ]
            }
        ],
    )

    with patch("app.services.input_storage.boto3.client", return_value=client):
        resp = auth_client.post(f"/api/load-plans/{pid}/steps/{step_id}/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_rows"] == 1
    assert body["matched_files"] == [
        {"filename": "good.csv", "row_count": 1},
        {"filename": "missing.csv", "row_count": 0},
    ]


# ── SFBL-166: name + input_from_step_id schema/validation ──────────────────────


_QUERY_STEP = {
    "object_name": "Account",
    "operation": "query",
    "soql": "SELECT Id FROM Account",
}


def _add_query_step(auth_client, plan_id: str, overrides=None) -> dict:
    payload = {**_QUERY_STEP, **(overrides or {})}
    return auth_client.post(f"/api/load-plans/{plan_id}/steps", json=payload).json()


def test_create_step_with_name_persists(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    body = _add_query_step(auth_client, pid, {"sequence": 1, "name": "accounts_q"})
    assert body["name"] == "accounts_q"


def test_create_step_name_is_trimmed(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    body = _add_query_step(auth_client, pid, {"sequence": 1, "name": "  accounts_q  "})
    assert body["name"] == "accounts_q"


def test_create_step_empty_name_persists_as_null(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    a = _add_query_step(auth_client, pid, {"sequence": 1, "name": ""})
    b = _add_query_step(
        auth_client, pid, {"sequence": 2, "name": "   ", "object_name": "Contact"}
    )
    # Both stored as NULL — partial unique index does not collide.
    assert a["name"] is None
    assert b["name"] is None


def test_create_step_duplicate_name_within_plan_rejected(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    _add_query_step(auth_client, pid, {"sequence": 1, "name": "dup"})
    resp = auth_client.post(
        f"/api/load-plans/{pid}/steps",
        json={**_QUERY_STEP, "sequence": 2, "name": "dup", "object_name": "Contact"},
    )
    assert resp.status_code == 422
    assert "already exists" in resp.json()["detail"]


def test_input_from_step_id_and_csv_pattern_mutually_exclusive(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    upstream = _add_query_step(auth_client, pid, {"sequence": 1})["id"]
    resp = auth_client.post(
        f"/api/load-plans/{pid}/steps",
        json={
            "sequence": 2,
            "object_name": "Account",
            "operation": "delete",
            "csv_file_pattern": "x*.csv",
            "input_from_step_id": upstream,
        },
    )
    assert resp.status_code == 422


def test_input_from_step_id_and_input_connection_id_mutually_exclusive(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    upstream = _add_query_step(auth_client, pid, {"sequence": 1})["id"]
    resp = auth_client.post(
        f"/api/load-plans/{pid}/steps",
        json={
            "sequence": 2,
            "object_name": "Account",
            "operation": "delete",
            "input_from_step_id": upstream,
            "input_connection_id": "local-output",
        },
    )
    assert resp.status_code == 422


def test_input_from_step_id_nonexistent_rejected(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    resp = auth_client.post(
        f"/api/load-plans/{pid}/steps",
        json={
            "sequence": 1,
            "object_name": "Account",
            "operation": "delete",
            "input_from_step_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert resp.status_code == 422
    assert "does not exist" in resp.json()["detail"]


def test_input_from_step_id_later_sequence_rejected(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    later = _add_query_step(auth_client, pid, {"sequence": 5})["id"]
    resp = auth_client.post(
        f"/api/load-plans/{pid}/steps",
        json={
            "sequence": 2,
            "object_name": "Account",
            "operation": "delete",
            "input_from_step_id": later,
        },
    )
    assert resp.status_code == 422


def test_input_from_step_id_non_query_rejected(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    upstream = _add_step(auth_client, pid, {"sequence": 1})["id"]  # insert op
    resp = auth_client.post(
        f"/api/load-plans/{pid}/steps",
        json={
            "sequence": 2,
            "object_name": "Account",
            "operation": "delete",
            "input_from_step_id": upstream,
        },
    )
    assert resp.status_code == 422
    assert "query" in resp.json()["detail"].lower()


def test_input_from_step_id_cross_plan_rejected(auth_client):
    cid = _conn_id(auth_client)
    pid_a = _plan_id(auth_client, cid)
    pid_b = _plan_id(auth_client, cid)
    upstream = _add_query_step(auth_client, pid_a, {"sequence": 1})["id"]
    resp = auth_client.post(
        f"/api/load-plans/{pid_b}/steps",
        json={
            "sequence": 1,
            "object_name": "Account",
            "operation": "delete",
            "input_from_step_id": upstream,
        },
    )
    assert resp.status_code == 422


def test_input_from_step_id_valid_chain_accepted(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    upstream = _add_query_step(auth_client, pid, {"sequence": 1})["id"]
    resp = auth_client.post(
        f"/api/load-plans/{pid}/steps",
        json={
            "sequence": 2,
            "object_name": "Account",
            "operation": "delete",
            "input_from_step_id": upstream,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["input_from_step_id"] == upstream


def test_update_step_set_input_from_step_id(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    upstream = _add_query_step(auth_client, pid, {"sequence": 1})["id"]
    # downstream initially has no input_from
    downstream = auth_client.post(
        f"/api/load-plans/{pid}/steps",
        json={
            "sequence": 2,
            "object_name": "Account",
            "operation": "delete",
            "csv_file_pattern": "x*.csv",
        },
    ).json()["id"]
    # Patch must clear csv_file_pattern at the same time to avoid the
    # mutual-exclusion error.
    resp = auth_client.put(
        f"/api/load-plans/{pid}/steps/{downstream}",
        json={"input_from_step_id": upstream, "csv_file_pattern": None},
    )
    assert resp.status_code == 200
    assert resp.json()["input_from_step_id"] == upstream


def test_update_step_input_from_step_id_self_reference_rejected(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    s = _add_query_step(auth_client, pid, {"sequence": 1, "name": "self"})["id"]
    resp = auth_client.put(
        f"/api/load-plans/{pid}/steps/{s}",
        json={"input_from_step_id": s},
    )
    assert resp.status_code == 422


def test_reorder_preserves_valid_references(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    q1 = _add_query_step(auth_client, pid, {"sequence": 1})["id"]
    q2 = _add_query_step(
        auth_client, pid, {"sequence": 2, "object_name": "Contact"}
    )["id"]
    dml = auth_client.post(
        f"/api/load-plans/{pid}/steps",
        json={
            "sequence": 3,
            "object_name": "Account",
            "operation": "delete",
            "input_from_step_id": q1,
        },
    ).json()["id"]
    # Swap the two query steps — dml's reference (q1) is still earlier than dml.
    resp = auth_client.post(
        f"/api/load-plans/{pid}/steps/reorder",
        json={"step_ids": [q2, q1, dml]},
    )
    assert resp.status_code == 200


def test_reorder_inverting_reference_rejected(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    q1 = _add_query_step(auth_client, pid, {"sequence": 1})["id"]
    dml = auth_client.post(
        f"/api/load-plans/{pid}/steps",
        json={
            "sequence": 2,
            "object_name": "Account",
            "operation": "delete",
            "input_from_step_id": q1,
        },
    ).json()["id"]
    # Put dml before q1 — would invert the reference.
    resp = auth_client.post(
        f"/api/load-plans/{pid}/steps/reorder",
        json={"step_ids": [dml, q1]},
    )
    assert resp.status_code == 422
    # DB state unchanged: original sequences preserved.
    listing = auth_client.get(f"/api/load-plans/{pid}").json()["load_steps"]
    assert {s["id"]: s["sequence"] for s in listing} == {q1: 1, dml: 2}


def test_preview_step_with_input_from_step_returns_note(auth_client):
    """SFBL-166: a DML step with input_from_step_id has neither pattern nor
    connection — preview must short-circuit to a descriptive note instead of
    500ing inside _validate_glob_pattern(None)."""
    pid = _plan_id(auth_client, _conn_id(auth_client))
    upstream = _add_query_step(auth_client, pid, {"sequence": 1, "name": "stale_accounts"})["id"]
    downstream = auth_client.post(
        f"/api/load-plans/{pid}/steps",
        json={
            "sequence": 2,
            "object_name": "Account",
            "operation": "delete",
            "input_from_step_id": upstream,
        },
    ).json()["id"]

    resp = auth_client.post(f"/api/load-plans/{pid}/steps/{downstream}/preview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "dml"
    assert body["matched_files"] == []
    assert body["total_rows"] == 0
    assert body["note"] is not None
    assert "stale_accounts" in body["note"]
