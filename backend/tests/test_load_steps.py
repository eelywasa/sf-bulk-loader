"""Tests for the /api/load-plans/{plan_id}/steps endpoints."""

import os
import tempfile
from unittest.mock import patch

import pytest

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

        with patch("app.api.load_steps.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = auth_client.post(f"/api/load-plans/{pid}/steps/{step_id}/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_rows"] == 4  # 2 rows * 2 files
    assert len(body["matched_files"]) == 2


def test_preview_step_no_files_returns_empty(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    step_id = _add_step(auth_client, pid, {"csv_file_pattern": "nonexistent_*.csv"})["id"]

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.api.load_steps.settings") as mock_settings:
            mock_settings.input_dir = tmpdir
            resp = auth_client.post(f"/api/load-plans/{pid}/steps/{step_id}/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_rows"] == 0
    assert body["matched_files"] == []


def test_preview_step_not_found_returns_404(auth_client):
    pid = _plan_id(auth_client, _conn_id(auth_client))
    resp = auth_client.post(f"/api/load-plans/{pid}/steps/bad-step/preview")
    assert resp.status_code == 404
