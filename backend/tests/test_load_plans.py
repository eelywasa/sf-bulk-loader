"""Tests for the /api/load-plans endpoints."""

import pytest

_CONN = {
    "name": "Test Org",
    "instance_url": "https://myorg.my.salesforce.com",
    "login_url": "https://login.salesforce.com",
    "client_id": "test_client_id",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nFAKEKEY\n-----END RSA PRIVATE KEY-----",
    "username": "test@example.com",
    "is_sandbox": False,
}

_PLAN = {
    "name": "Q1 Migration",
    "description": "Quarterly data load",
    "abort_on_step_failure": True,
    "error_threshold_pct": 5.0,
    "max_parallel_jobs": 3,
}


def _create_connection(auth_client) -> str:
    return auth_client.post("/api/connections/", json=_CONN).json()["id"]


def _create_plan(auth_client, connection_id: str, overrides=None) -> dict:
    payload = {**_PLAN, "connection_id": connection_id, **(overrides or {})}
    return auth_client.post("/api/load-plans/", json=payload).json()


# ── Create ─────────────────────────────────────────────────────────────────────


def test_create_plan_returns_201(auth_client):
    conn_id = _create_connection(auth_client)
    payload = {**_PLAN, "connection_id": conn_id}
    resp = auth_client.post("/api/load-plans/", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == _PLAN["name"]
    assert body["connection_id"] == conn_id
    assert "id" in body
    assert body["load_steps"] == []


def test_create_plan_invalid_connection_returns_404(auth_client):
    resp = auth_client.post("/api/load-plans/", json={**_PLAN, "connection_id": "bad-id"})
    assert resp.status_code == 404


def test_create_plan_missing_name_returns_422(auth_client):
    conn_id = _create_connection(auth_client)
    resp = auth_client.post("/api/load-plans/", json={"connection_id": conn_id})
    assert resp.status_code == 422


# ── List ───────────────────────────────────────────────────────────────────────


def test_list_plans_empty(auth_client):
    resp = auth_client.get("/api/load-plans/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_plans_returns_all(auth_client):
    conn_id = _create_connection(auth_client)
    _create_plan(auth_client, conn_id)
    _create_plan(auth_client, conn_id, {"name": "Plan B"})
    resp = auth_client.get("/api/load-plans/")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ── Get ────────────────────────────────────────────────────────────────────────


def test_get_plan_returns_plan_with_steps(auth_client):
    conn_id = _create_connection(auth_client)
    plan_id = _create_plan(auth_client, conn_id)["id"]
    resp = auth_client.get(f"/api/load-plans/{plan_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == plan_id
    assert "load_steps" in body


def test_get_plan_not_found_returns_404(auth_client):
    resp = auth_client.get("/api/load-plans/nonexistent")
    assert resp.status_code == 404


# ── Update ─────────────────────────────────────────────────────────────────────


def test_update_plan_name(auth_client):
    conn_id = _create_connection(auth_client)
    plan_id = _create_plan(auth_client, conn_id)["id"]
    resp = auth_client.put(f"/api/load-plans/{plan_id}", json={"name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


def test_update_plan_invalid_connection_returns_404(auth_client):
    conn_id = _create_connection(auth_client)
    plan_id = _create_plan(auth_client, conn_id)["id"]
    resp = auth_client.put(f"/api/load-plans/{plan_id}", json={"connection_id": "bad-id"})
    assert resp.status_code == 404


def test_update_plan_not_found_returns_404(auth_client):
    resp = auth_client.put("/api/load-plans/bad-id", json={"name": "X"})
    assert resp.status_code == 404


# ── Delete ─────────────────────────────────────────────────────────────────────


def test_delete_plan_returns_204(auth_client):
    conn_id = _create_connection(auth_client)
    plan_id = _create_plan(auth_client, conn_id)["id"]
    resp = auth_client.delete(f"/api/load-plans/{plan_id}")
    assert resp.status_code == 204


def test_delete_plan_removes_record(auth_client):
    conn_id = _create_connection(auth_client)
    plan_id = _create_plan(auth_client, conn_id)["id"]
    auth_client.delete(f"/api/load-plans/{plan_id}")
    assert auth_client.get(f"/api/load-plans/{plan_id}").status_code == 404


def test_delete_plan_not_found_returns_404(auth_client):
    assert auth_client.delete("/api/load-plans/nonexistent").status_code == 404


def test_delete_plan_cascades_to_steps(auth_client):
    conn_id = _create_connection(auth_client)
    plan_id = _create_plan(auth_client, conn_id)["id"]
    # Add a step
    auth_client.post(
        f"/api/load-plans/{plan_id}/steps",
        json={
            "sequence": 1,
            "object_name": "Account",
            "operation": "insert",
            "csv_file_pattern": "accounts*.csv",
        },
    )
    auth_client.delete(f"/api/load-plans/{plan_id}")
    # Plan and its steps should be gone
    assert auth_client.get(f"/api/load-plans/{plan_id}").status_code == 404


# ── Duplicate ──────────────────────────────────────────────────────────────────

_STEP = {
    "sequence": 1,
    "object_name": "Account",
    "operation": "insert",
    "csv_file_pattern": "accounts*.csv",
    "partition_size": 5000,
}


def test_duplicate_plan_returns_201(auth_client):
    conn_id = _create_connection(auth_client)
    source = _create_plan(auth_client, conn_id)
    auth_client.post(f"/api/load-plans/{source['id']}/steps", json=_STEP)

    resp = auth_client.post(f"/api/load-plans/{source['id']}/duplicate")
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] != source["id"]
    assert body["name"] == f"Copy of {source['name']}"
    assert body["connection_id"] == source["connection_id"]
    assert len(body["load_steps"]) == 1


def test_duplicate_plan_copies_steps(auth_client):
    conn_id = _create_connection(auth_client)
    source = _create_plan(auth_client, conn_id)
    auth_client.post(f"/api/load-plans/{source['id']}/steps", json=_STEP)

    copy = auth_client.post(f"/api/load-plans/{source['id']}/duplicate").json()
    src_step = auth_client.get(f"/api/load-plans/{source['id']}").json()["load_steps"][0]
    copy_step = copy["load_steps"][0]

    assert copy_step["id"] != src_step["id"]
    assert copy_step["load_plan_id"] == copy["id"]
    assert copy_step["object_name"] == src_step["object_name"]
    assert copy_step["operation"] == src_step["operation"]
    assert copy_step["csv_file_pattern"] == src_step["csv_file_pattern"]
    assert copy_step["partition_size"] == src_step["partition_size"]
    assert copy_step["sequence"] == src_step["sequence"]


def test_duplicate_plan_not_found_returns_404(auth_client):
    resp = auth_client.post("/api/load-plans/nonexistent/duplicate")
    assert resp.status_code == 404


# ── Start run ──────────────────────────────────────────────────────────────────


def test_start_run_returns_201(auth_client):
    conn_id = _create_connection(auth_client)
    plan_id = _create_plan(auth_client, conn_id)["id"]
    resp = auth_client.post(f"/api/load-plans/{plan_id}/run")
    assert resp.status_code == 201
    body = resp.json()
    assert body["load_plan_id"] == plan_id
    assert body["status"] == "pending"
    assert body["initiated_by"] == "test-user@example.com"


def test_start_run_plan_not_found_returns_404(auth_client):
    resp = auth_client.post("/api/load-plans/bad-plan/run", json={})
    assert resp.status_code == 404
