"""Tests for the /api/runs endpoints."""

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


def _setup(auth_client) -> tuple[str, str]:
    """Create a connection and plan; return (conn_id, plan_id)."""
    conn_id = auth_client.post("/api/connections/", json=_CONN).json()["id"]
    plan_id = auth_client.post(
        "/api/load-plans/",
        json={"name": "Migration Plan", "connection_id": conn_id},
    ).json()["id"]
    return conn_id, plan_id


def _start_run(auth_client, plan_id: str) -> dict:
    return auth_client.post(f"/api/load-plans/{plan_id}/run").json()


# ── List ───────────────────────────────────────────────────────────────────────


def test_list_runs_empty(auth_client):
    resp = auth_client.get("/api/runs/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_runs_returns_all(auth_client):
    _, plan_id = _setup(auth_client)
    _start_run(auth_client, plan_id)
    _start_run(auth_client, plan_id)
    runs = auth_client.get("/api/runs/").json()
    assert len(runs) == 2


def test_list_runs_filter_by_plan_id(auth_client):
    conn_id = auth_client.post("/api/connections/", json=_CONN).json()["id"]
    plan1 = auth_client.post("/api/load-plans/", json={"name": "P1", "connection_id": conn_id}).json()["id"]
    plan2 = auth_client.post("/api/load-plans/", json={"name": "P2", "connection_id": conn_id}).json()["id"]
    _start_run(auth_client, plan1)
    _start_run(auth_client, plan2)

    runs = auth_client.get(f"/api/runs/?plan_id={plan1}").json()
    assert len(runs) == 1
    assert runs[0]["load_plan_id"] == plan1


def test_list_runs_filter_by_status(auth_client):
    _, plan_id = _setup(auth_client)
    run_id = _start_run(auth_client, plan_id)["id"]
    # Abort the run
    auth_client.post(f"/api/runs/{run_id}/abort")

    aborted = auth_client.get("/api/runs/?run_status=aborted").json()
    assert any(r["id"] == run_id for r in aborted)

    pending = auth_client.get("/api/runs/?run_status=pending").json()
    assert not any(r["id"] == run_id for r in pending)


# ── Get detail ────────────────────────────────────────────────────────────────


def test_get_run_returns_detail(auth_client):
    _, plan_id = _setup(auth_client)
    run_id = _start_run(auth_client, plan_id)["id"]
    resp = auth_client.get(f"/api/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == run_id
    assert "jobs" in body


def test_get_run_not_found_returns_404(auth_client):
    assert auth_client.get("/api/runs/nonexistent").status_code == 404


# ── Abort ──────────────────────────────────────────────────────────────────────


def test_abort_pending_run(auth_client):
    _, plan_id = _setup(auth_client)
    run_id = _start_run(auth_client, plan_id)["id"]
    resp = auth_client.post(f"/api/runs/{run_id}/abort")
    assert resp.status_code == 200
    assert resp.json()["status"] == "aborted"


def test_abort_already_aborted_run_returns_409(auth_client):
    _, plan_id = _setup(auth_client)
    run_id = _start_run(auth_client, plan_id)["id"]
    auth_client.post(f"/api/runs/{run_id}/abort")  # first abort
    resp = auth_client.post(f"/api/runs/{run_id}/abort")  # second abort
    assert resp.status_code == 409


def test_abort_nonexistent_run_returns_404(auth_client):
    assert auth_client.post("/api/runs/bad-id/abort").status_code == 404


# ── Summary ────────────────────────────────────────────────────────────────────


def test_run_summary_no_jobs(auth_client):
    _, plan_id = _setup(auth_client)
    run_id = _start_run(auth_client, plan_id)["id"]
    resp = auth_client.get(f"/api/runs/{run_id}/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["total_records"] == 0
    assert body["steps"] == []


def test_run_summary_not_found_returns_404(auth_client):
    assert auth_client.get("/api/runs/bad-id/summary").status_code == 404


def test_run_summary_aggregates_jobs(auth_client):
    """Summary correctly sums records_processed and records_failed across jobs."""
    from tests.conftest import _TestSession, _run_async
    from app.models.job import JobRecord, JobStatus
    from app.models.load_step import LoadStep, Operation

    _, plan_id = _setup(auth_client)
    run_id = _start_run(auth_client, plan_id)["id"]

    # Create a step and two job records directly via DB
    async def _seed():
        async with _TestSession() as session:
            step = LoadStep(
                load_plan_id=plan_id,
                sequence=1,
                object_name="Account",
                operation=Operation.insert,
                csv_file_pattern="*.csv",
            )
            session.add(step)
            await session.flush()

            for idx, (processed, failed) in enumerate([(100, 5), (200, 10)]):
                job = JobRecord(
                    load_run_id=run_id,
                    load_step_id=step.id,
                    partition_index=idx,
                    status=JobStatus.job_complete,
                    records_processed=processed,
                    records_failed=failed,
                )
                session.add(job)
            await session.commit()

    _run_async(_seed())

    resp = auth_client.get(f"/api/runs/{run_id}/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_records"] == 300
    assert body["total_errors"] == 15
    assert body["total_success"] == 285
    assert len(body["steps"]) == 1
    assert body["steps"][0]["object_name"] == "Account"
    assert body["steps"][0]["job_count"] == 2
