"""Tests for SFBL-159: InputConnection.direction + LoadPlan.output_connection_id."""

import pytest

# ── Shared fixtures ────────────────────────────────────────────────────────────

_CONN = {
    "name": "Test Org",
    "instance_url": "https://myorg.my.salesforce.com",
    "login_url": "https://login.salesforce.com",
    "client_id": "3MVG9test_client_id",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nFAKEKEY\n-----END RSA PRIVATE KEY-----",
    "username": "test@example.com",
    "is_sandbox": False,
}

_IC_BASE = {
    "name": "My S3 Bucket",
    "provider": "s3",
    "bucket": "my-bucket",
    "root_prefix": "data/",
    "region": "us-east-1",
    "access_key_id": "AKIAIOSFODNN7EXAMPLE",
    "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
}

_PLAN_BASE = {
    "name": "Test Plan",
    "abort_on_step_failure": False,
    "error_threshold_pct": 5.0,
    "max_parallel_jobs": 1,
}

_STEP_BASE = {
    "object_name": "Account",
    "operation": "insert",
    "csv_file_pattern": "/data/*.csv",
}


def _create_ic(auth_client, direction="in", **extra):
    payload = {**_IC_BASE, "direction": direction, **extra}
    resp = auth_client.post("/api/input-connections/", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_conn(auth_client):
    resp = auth_client.post("/api/connections/", json=_CONN)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_plan(auth_client, conn_id, **extra):
    payload = {**_PLAN_BASE, "connection_id": conn_id, **extra}
    resp = auth_client.post("/api/load-plans/", json=payload)
    return resp


# ── Schema serialisation ───────────────────────────────────────────────────────


def test_input_connection_direction_defaults_to_in(auth_client):
    """Creating an IC without specifying direction should default to 'in'."""
    body = _create_ic(auth_client)
    assert body["direction"] == "in"


def test_input_connection_direction_in_response(auth_client):
    """direction field is present in both create and get responses."""
    body = _create_ic(auth_client, direction="out")
    assert body["direction"] == "out"

    get_body = auth_client.get(f"/api/input-connections/{body['id']}").json()
    assert get_body["direction"] == "out"


def test_input_connection_direction_both(auth_client):
    body = _create_ic(auth_client, direction="both")
    assert body["direction"] == "both"


def test_input_connection_direction_invalid_rejected(auth_client):
    payload = {**_IC_BASE, "direction": "invalid"}
    resp = auth_client.post("/api/input-connections/", json=payload)
    assert resp.status_code == 422


def test_load_plan_output_connection_id_defaults_to_none(auth_client):
    conn_id = _create_conn(auth_client)
    resp = _create_plan(auth_client, conn_id)
    assert resp.status_code == 201
    assert resp.json()["output_connection_id"] is None


def test_load_plan_output_connection_id_in_response(auth_client):
    ic = _create_ic(auth_client, direction="out")
    conn_id = _create_conn(auth_client)
    resp = _create_plan(auth_client, conn_id, output_connection_id=ic["id"])
    assert resp.status_code == 201
    assert resp.json()["output_connection_id"] == ic["id"]


# ── GET /api/input-connections/?direction= filtering ──────────────────────────


def test_list_direction_filter_out_returns_out_and_both(auth_client):
    """?direction=out should return connections with direction 'out' or 'both'."""
    _create_ic(auth_client, direction="in", name="In Bucket")
    _create_ic(auth_client, direction="out", name="Out Bucket")
    _create_ic(auth_client, direction="both", name="Both Bucket")

    resp = auth_client.get("/api/input-connections/?direction=out")
    assert resp.status_code == 200
    names = {ic["name"] for ic in resp.json()}
    assert "Out Bucket" in names
    assert "Both Bucket" in names
    assert "In Bucket" not in names


def test_list_direction_filter_in_returns_in_and_both(auth_client):
    """?direction=in should return 'in' and 'both' connections (input-capable)."""
    _create_ic(auth_client, direction="in", name="In Bucket")
    _create_ic(auth_client, direction="out", name="Out Bucket")
    _create_ic(auth_client, direction="both", name="Both Bucket")

    resp = auth_client.get("/api/input-connections/?direction=in")
    assert resp.status_code == 200
    names = {ic["name"] for ic in resp.json()}
    assert "In Bucket" in names
    assert "Both Bucket" in names
    assert "Out Bucket" not in names


def test_list_no_direction_filter_returns_all(auth_client):
    """Without ?direction param, all connections are returned."""
    _create_ic(auth_client, direction="in", name="In Bucket")
    _create_ic(auth_client, direction="out", name="Out Bucket")

    resp = auth_client.get("/api/input-connections/")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ── Plan create/update validation ─────────────────────────────────────────────


def test_create_plan_with_output_connection_direction_in_returns_422(auth_client):
    """output_connection_id pointing to a direction='in' connection → 422."""
    ic = _create_ic(auth_client, direction="in")
    conn_id = _create_conn(auth_client)
    resp = _create_plan(auth_client, conn_id, output_connection_id=ic["id"])
    assert resp.status_code == 422
    assert "direction" in resp.json()["detail"].lower() or "out" in resp.json()["detail"].lower()


def test_create_plan_with_output_connection_direction_out_ok(auth_client):
    ic = _create_ic(auth_client, direction="out")
    conn_id = _create_conn(auth_client)
    resp = _create_plan(auth_client, conn_id, output_connection_id=ic["id"])
    assert resp.status_code == 201
    assert resp.json()["output_connection_id"] == ic["id"]


def test_create_plan_with_output_connection_direction_both_ok(auth_client):
    ic = _create_ic(auth_client, direction="both")
    conn_id = _create_conn(auth_client)
    resp = _create_plan(auth_client, conn_id, output_connection_id=ic["id"])
    assert resp.status_code == 201


def test_update_plan_set_output_connection_direction_in_returns_422(auth_client):
    """PUT plan with output_connection_id pointing to direction='in' → 422."""
    ic_in = _create_ic(auth_client, direction="in")
    conn_id = _create_conn(auth_client)
    plan_id = _create_plan(auth_client, conn_id).json()["id"]

    resp = auth_client.put(
        f"/api/load-plans/{plan_id}",
        json={"output_connection_id": ic_in["id"]},
    )
    assert resp.status_code == 422


def test_update_plan_clear_output_connection_ok(auth_client):
    """PUT plan with output_connection_id=None should clear it without validation."""
    ic_out = _create_ic(auth_client, direction="out")
    conn_id = _create_conn(auth_client)
    plan_id = _create_plan(auth_client, conn_id, output_connection_id=ic_out["id"]).json()["id"]

    resp = auth_client.put(f"/api/load-plans/{plan_id}", json={"output_connection_id": None})
    assert resp.status_code == 200
    assert resp.json()["output_connection_id"] is None


# ── Step create/update validation ─────────────────────────────────────────────


def _make_step(auth_client, plan_id, ic_id=None):
    payload = {**_STEP_BASE}
    if ic_id is not None:
        payload["input_connection_id"] = ic_id
    return auth_client.post(f"/api/load-plans/{plan_id}/steps", json=payload)


def test_step_create_with_input_connection_direction_out_returns_422(auth_client):
    """Creating step with input_connection_id pointing to direction='out' → 422."""
    ic = _create_ic(auth_client, direction="out")
    conn_id = _create_conn(auth_client)
    plan_id = _create_plan(auth_client, conn_id).json()["id"]

    resp = _make_step(auth_client, plan_id, ic_id=ic["id"])
    assert resp.status_code == 422
    assert "direction" in resp.json()["detail"].lower() or "in" in resp.json()["detail"].lower()


def test_step_create_with_input_connection_direction_in_ok(auth_client):
    ic = _create_ic(auth_client, direction="in")
    conn_id = _create_conn(auth_client)
    plan_id = _create_plan(auth_client, conn_id).json()["id"]

    resp = _make_step(auth_client, plan_id, ic_id=ic["id"])
    assert resp.status_code == 201
    assert resp.json()["input_connection_id"] == ic["id"]


def test_step_create_with_input_connection_direction_both_ok(auth_client):
    ic = _create_ic(auth_client, direction="both")
    conn_id = _create_conn(auth_client)
    plan_id = _create_plan(auth_client, conn_id).json()["id"]

    resp = _make_step(auth_client, plan_id, ic_id=ic["id"])
    assert resp.status_code == 201


def test_step_update_with_input_connection_direction_out_returns_422(auth_client):
    """PUT step updating input_connection_id to direction='out' → 422."""
    ic_in = _create_ic(auth_client, direction="in")
    ic_out = _create_ic(auth_client, direction="out", name="Out Bucket 2")
    conn_id = _create_conn(auth_client)
    plan_id = _create_plan(auth_client, conn_id).json()["id"]

    step_id = _make_step(auth_client, plan_id, ic_id=ic_in["id"]).json()["id"]

    resp = auth_client.put(
        f"/api/load-plans/{plan_id}/steps/{step_id}",
        json={"input_connection_id": ic_out["id"]},
    )
    assert resp.status_code == 422


# ── DELETE blocked by output_connection_id references ─────────────────────────


def test_delete_input_connection_returns_409_when_used_as_output(auth_client):
    """Deleting an IC used as output_connection_id on a plan → 409."""
    ic = _create_ic(auth_client, direction="out")
    conn_id = _create_conn(auth_client)
    _create_plan(auth_client, conn_id, output_connection_id=ic["id"])

    resp = auth_client.delete(f"/api/input-connections/{ic['id']}")
    assert resp.status_code == 409
    assert "output destination" in resp.json()["detail"].lower()


def test_delete_input_connection_succeeds_when_not_referenced_as_output(auth_client):
    """Deleting an IC that is NOT used as output on any plan → 204."""
    ic = _create_ic(auth_client, direction="out")
    resp = auth_client.delete(f"/api/input-connections/{ic['id']}")
    assert resp.status_code == 204


def test_delete_input_connection_returns_409_when_used_as_step_input(auth_client):
    """Deleting an IC used by a LoadStep as input → 409 (existing RESTRICT behaviour)."""
    ic = _create_ic(auth_client, direction="in")
    conn_id = _create_conn(auth_client)
    plan_id = _create_plan(auth_client, conn_id).json()["id"]
    _make_step(auth_client, plan_id, ic_id=ic["id"])

    resp = auth_client.delete(f"/api/input-connections/{ic['id']}")
    assert resp.status_code == 409


# ── direction update ───────────────────────────────────────────────────────────


def test_update_input_connection_direction(auth_client):
    ic = _create_ic(auth_client, direction="in")
    resp = auth_client.put(f"/api/input-connections/{ic['id']}", json={"direction": "both"})
    assert resp.status_code == 200
    assert resp.json()["direction"] == "both"
