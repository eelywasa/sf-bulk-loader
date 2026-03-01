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


def _create_connection(client) -> str:
    return client.post("/api/connections/", json=_CONN).json()["id"]


def _create_plan(client, connection_id: str, overrides=None) -> dict:
    payload = {**_PLAN, "connection_id": connection_id, **(overrides or {})}
    return client.post("/api/load-plans/", json=payload).json()


# ── Create ─────────────────────────────────────────────────────────────────────


def test_create_plan_returns_201(client):
    conn_id = _create_connection(client)
    payload = {**_PLAN, "connection_id": conn_id}
    resp = client.post("/api/load-plans/", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == _PLAN["name"]
    assert body["connection_id"] == conn_id
    assert "id" in body
    assert body["load_steps"] == []


def test_create_plan_invalid_connection_returns_404(client):
    resp = client.post("/api/load-plans/", json={**_PLAN, "connection_id": "bad-id"})
    assert resp.status_code == 404


def test_create_plan_missing_name_returns_422(client):
    conn_id = _create_connection(client)
    resp = client.post("/api/load-plans/", json={"connection_id": conn_id})
    assert resp.status_code == 422


# ── List ───────────────────────────────────────────────────────────────────────


def test_list_plans_empty(client):
    resp = client.get("/api/load-plans/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_plans_returns_all(client):
    conn_id = _create_connection(client)
    _create_plan(client, conn_id)
    _create_plan(client, conn_id, {"name": "Plan B"})
    resp = client.get("/api/load-plans/")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ── Get ────────────────────────────────────────────────────────────────────────


def test_get_plan_returns_plan_with_steps(client):
    conn_id = _create_connection(client)
    plan_id = _create_plan(client, conn_id)["id"]
    resp = client.get(f"/api/load-plans/{plan_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == plan_id
    assert "load_steps" in body


def test_get_plan_not_found_returns_404(client):
    resp = client.get("/api/load-plans/nonexistent")
    assert resp.status_code == 404


# ── Update ─────────────────────────────────────────────────────────────────────


def test_update_plan_name(client):
    conn_id = _create_connection(client)
    plan_id = _create_plan(client, conn_id)["id"]
    resp = client.put(f"/api/load-plans/{plan_id}", json={"name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


def test_update_plan_invalid_connection_returns_404(client):
    conn_id = _create_connection(client)
    plan_id = _create_plan(client, conn_id)["id"]
    resp = client.put(f"/api/load-plans/{plan_id}", json={"connection_id": "bad-id"})
    assert resp.status_code == 404


def test_update_plan_not_found_returns_404(client):
    resp = client.put("/api/load-plans/bad-id", json={"name": "X"})
    assert resp.status_code == 404


# ── Delete ─────────────────────────────────────────────────────────────────────


def test_delete_plan_returns_204(client):
    conn_id = _create_connection(client)
    plan_id = _create_plan(client, conn_id)["id"]
    resp = client.delete(f"/api/load-plans/{plan_id}")
    assert resp.status_code == 204


def test_delete_plan_removes_record(client):
    conn_id = _create_connection(client)
    plan_id = _create_plan(client, conn_id)["id"]
    client.delete(f"/api/load-plans/{plan_id}")
    assert client.get(f"/api/load-plans/{plan_id}").status_code == 404


def test_delete_plan_not_found_returns_404(client):
    assert client.delete("/api/load-plans/nonexistent").status_code == 404


def test_delete_plan_cascades_to_steps(client):
    conn_id = _create_connection(client)
    plan_id = _create_plan(client, conn_id)["id"]
    # Add a step
    client.post(
        f"/api/load-plans/{plan_id}/steps",
        json={
            "sequence": 1,
            "object_name": "Account",
            "operation": "insert",
            "csv_file_pattern": "accounts*.csv",
        },
    )
    client.delete(f"/api/load-plans/{plan_id}")
    # Plan and its steps should be gone
    assert client.get(f"/api/load-plans/{plan_id}").status_code == 404


# ── Start run ──────────────────────────────────────────────────────────────────


def test_start_run_returns_201(client):
    conn_id = _create_connection(client)
    plan_id = _create_plan(client, conn_id)["id"]
    resp = client.post(f"/api/load-plans/{plan_id}/run", json={"initiated_by": "tester"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["load_plan_id"] == plan_id
    assert body["status"] == "pending"
    assert body["initiated_by"] == "tester"


def test_start_run_plan_not_found_returns_404(client):
    resp = client.post("/api/load-plans/bad-plan/run", json={})
    assert resp.status_code == 404
