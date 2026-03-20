"""Tests for POST /runs/{id}/retry-step/{step_id} endpoint and prepare_retry_step service."""

import asyncio
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.job import JobRecord, JobStatus
from app.models.load_run import LoadRun, RunStatus

# ── Secondary DB session (same file as conftest's test_api.db) ─────────────────

_TEST_DB_URL = "sqlite+aiosqlite:///./test_api.db"
_engine = create_async_engine(_TEST_DB_URL, echo=False)
_S = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Shared seed helpers ────────────────────────────────────────────────────────

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


def _setup(auth_client) -> tuple[str, str, str]:
    """Create connection + plan + step; return (conn_id, plan_id, step_id)."""
    conn_id = auth_client.post("/api/connections/", json=_CONN).json()["id"]
    plan_id = auth_client.post(
        "/api/load-plans/",
        json={"name": "Test Plan", "connection_id": conn_id},
    ).json()["id"]
    step_id = auth_client.post(
        f"/api/load-plans/{plan_id}/steps", json=_STEP
    ).json()["id"]
    return conn_id, plan_id, step_id


def _seed_run(plan_id: str, status: RunStatus) -> str:
    """Insert a LoadRun directly into the test DB; return run_id."""

    async def _create():
        async with _S() as db:
            run = LoadRun(load_plan_id=plan_id, status=status, initiated_by="test")
            db.add(run)
            await db.commit()
            await db.refresh(run)
            return run.id

    return _run(_create())


def _seed_job(
    run_id: str,
    step_id: str,
    *,
    status: JobStatus = JobStatus.failed,
    error_file_path: str | None = None,
    unprocessed_file_path: str | None = None,
) -> str:
    """Insert a JobRecord directly; return job_id."""

    async def _create():
        async with _S() as db:
            job = JobRecord(
                load_run_id=run_id,
                load_step_id=step_id,
                partition_index=0,
                status=status,
                error_file_path=error_file_path,
                unprocessed_file_path=unprocessed_file_path,
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            return job.id

    return _run(_create())


def _write_error_csv(tmp_path, run_id: str, step_id: str) -> str:
    """Write a minimal SF Bulk API error CSV; return the relative path from output_dir."""
    rel = f"{run_id}/{step_id}/partition_0_error.csv"
    full = tmp_path / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text("sf__Id,sf__Error,Name,ExternalId__c\n,FIELD_ERROR,Acme Corp,EXT-001\n")
    return rel


def _write_unprocessed_csv(tmp_path, run_id: str, step_id: str) -> str:
    """Write a minimal unprocessed CSV; return the relative path from output_dir."""
    rel = f"{run_id}/{step_id}/partition_0_unprocessed.csv"
    full = tmp_path / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text("Name,ExternalId__c\nAcme Corp,EXT-001\n")
    return rel


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_retry_step_returns_201_with_new_run(auth_client, tmp_path):
    _, plan_id, step_id = _setup(auth_client)
    run_id = _seed_run(plan_id, RunStatus.completed)
    rel = _write_error_csv(tmp_path, run_id, step_id)
    _seed_job(run_id, step_id, status=JobStatus.failed, error_file_path=rel)

    with (
        patch("app.services.load_run_service.settings") as mock_s,
        patch("app.services.orchestrator.execute_retry_run", new=AsyncMock()),
    ):
        mock_s.output_dir = str(tmp_path)
        resp = auth_client.post(f"/api/runs/{run_id}/retry-step/{step_id}")

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["retry_of_run_id"] == run_id


def test_retry_step_links_new_run_to_parent(auth_client, tmp_path):
    _, plan_id, step_id = _setup(auth_client)
    run_id = _seed_run(plan_id, RunStatus.completed)
    rel = _write_error_csv(tmp_path, run_id, step_id)
    _seed_job(run_id, step_id, status=JobStatus.failed, error_file_path=rel)

    with (
        patch("app.services.load_run_service.settings") as mock_s,
        patch("app.services.orchestrator.execute_retry_run", new=AsyncMock()),
    ):
        mock_s.output_dir = str(tmp_path)
        new_run_id = auth_client.post(f"/api/runs/{run_id}/retry-step/{step_id}").json()["id"]

    detail = auth_client.get(f"/api/runs/{new_run_id}").json()
    assert detail["retry_of_run_id"] == run_id


def test_retry_step_run_not_found_returns_404(auth_client):
    resp = auth_client.post("/api/runs/nonexistent/retry-step/also-nonexistent")
    assert resp.status_code == 404


def test_retry_step_no_retryable_jobs_returns_422(auth_client):
    _, plan_id, step_id = _setup(auth_client)
    run_id = _seed_run(plan_id, RunStatus.completed)
    # job_complete with no file paths → not retryable
    _seed_job(run_id, step_id, status=JobStatus.job_complete)

    resp = auth_client.post(f"/api/runs/{run_id}/retry-step/{step_id}")

    assert resp.status_code == 422
    assert "No retryable" in resp.json()["detail"]


def test_retry_step_run_not_terminal_returns_409(auth_client):
    _, plan_id, step_id = _setup(auth_client)
    run_id = _seed_run(plan_id, RunStatus.running)

    resp = auth_client.post(f"/api/runs/{run_id}/retry-step/{step_id}")

    assert resp.status_code == 409
    assert "terminal state" in resp.json()["detail"]


def test_retry_step_run_pending_returns_409(auth_client):
    _, plan_id, step_id = _setup(auth_client)
    run_id = _seed_run(plan_id, RunStatus.pending)

    resp = auth_client.post(f"/api/runs/{run_id}/retry-step/{step_id}")

    assert resp.status_code == 409


def test_retry_step_step_not_found_returns_404(auth_client):
    """Retryable jobs exist for the step_id but the LoadStep row doesn't → 404."""
    conn_id = auth_client.post("/api/connections/", json=_CONN).json()["id"]
    plan_id = auth_client.post(
        "/api/load-plans/",
        json={"name": "Test Plan", "connection_id": conn_id},
    ).json()["id"]
    run_id = _seed_run(plan_id, RunStatus.completed)

    # Use a step_id that was never inserted into the DB.
    # SQLite doesn't enforce FK by default so the job can reference it.
    import uuid
    fake_step_id = str(uuid.uuid4())
    _seed_job(run_id, fake_step_id, status=JobStatus.failed)

    resp = auth_client.post(f"/api/runs/{run_id}/retry-step/{fake_step_id}")

    assert resp.status_code == 404
    assert "Step not found" in resp.json()["detail"]


def test_retry_step_all_four_terminal_statuses_accepted(auth_client, tmp_path):
    """All four terminal run statuses allow retry."""
    for run_status in (
        RunStatus.completed,
        RunStatus.completed_with_errors,
        RunStatus.failed,
        RunStatus.aborted,
    ):
        _, plan_id, step_id = _setup(auth_client)
        run_id = _seed_run(plan_id, run_status)
        rel = _write_error_csv(tmp_path, run_id, step_id)
        _seed_job(run_id, step_id, status=JobStatus.failed, error_file_path=rel)

        with (
            patch("app.services.load_run_service.settings") as mock_s,
            patch("app.services.orchestrator.execute_retry_run", new=AsyncMock()),
        ):
            mock_s.output_dir = str(tmp_path)
            resp = auth_client.post(f"/api/runs/{run_id}/retry-step/{step_id}")

        assert resp.status_code == 201, (
            f"Expected 201 for status={run_status.value}, got {resp.status_code}: {resp.text}"
        )


def test_retry_step_unprocessed_file_qualifies_job(auth_client, tmp_path):
    """A job_complete job with an unprocessed_file_path qualifies for retry."""
    _, plan_id, step_id = _setup(auth_client)
    run_id = _seed_run(plan_id, RunStatus.completed)
    rel = _write_unprocessed_csv(tmp_path, run_id, step_id)
    _seed_job(run_id, step_id, status=JobStatus.job_complete, unprocessed_file_path=rel)

    with (
        patch("app.services.load_run_service.settings") as mock_s,
        patch("app.services.orchestrator.execute_retry_run", new=AsyncMock()),
    ):
        mock_s.output_dir = str(tmp_path)
        resp = auth_client.post(f"/api/runs/{run_id}/retry-step/{step_id}")

    assert resp.status_code == 201
