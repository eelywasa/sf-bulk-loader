"""Tests for the /api/runs/{run_id}/jobs and /api/jobs/{id} endpoints."""

import os
import tempfile

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


def _setup_run(client) -> tuple[str, str, str]:
    """Create connection + plan + run; return (conn_id, plan_id, run_id)."""
    conn_id = client.post("/api/connections/", json=_CONN).json()["id"]
    plan_id = client.post(
        "/api/load-plans/",
        json={"name": "Plan", "connection_id": conn_id},
    ).json()["id"]
    run_id = client.post(
        f"/api/load-plans/{plan_id}/run",
        json={"initiated_by": "test"},
    ).json()["id"]
    return conn_id, plan_id, run_id


def _seed_jobs(plan_id: str, run_id: str, n: int = 2) -> list[str]:
    """Insert *n* JobRecord rows directly via DB. Return their IDs."""
    from app.models.job import JobRecord, JobStatus
    from app.models.load_step import LoadStep, Operation
    from tests.conftest import _TestSession, _run_async

    ids: list[str] = []

    async def _insert():
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
            for i in range(n):
                job = JobRecord(
                    load_run_id=run_id,
                    load_step_id=step.id,
                    partition_index=i,
                    status=JobStatus.job_complete,
                    records_processed=100,
                    records_failed=0,
                )
                session.add(job)
                await session.flush()
                ids.append(job.id)
            await session.commit()
        return ids

    _run_async(_insert())
    return ids


# ── List jobs ──────────────────────────────────────────────────────────────────


def test_list_jobs_empty(client):
    _, _, run_id = _setup_run(client)
    resp = client.get(f"/api/runs/{run_id}/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_jobs_returns_jobs(client):
    _, plan_id, run_id = _setup_run(client)
    _seed_jobs(plan_id, run_id, n=3)
    resp = client.get(f"/api/runs/{run_id}/jobs")
    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_list_jobs_filter_by_status(client):
    from app.models.job import JobStatus
    from app.models.load_step import LoadStep, Operation
    from tests.conftest import _TestSession, _run_async

    _, plan_id, run_id = _setup_run(client)

    async def _insert_mixed():
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
            from app.models.job import JobRecord
            for i, st in enumerate([JobStatus.job_complete, JobStatus.failed, JobStatus.pending]):
                session.add(JobRecord(
                    load_run_id=run_id,
                    load_step_id=step.id,
                    partition_index=i,
                    status=st,
                ))
            await session.commit()

    _run_async(_insert_mixed())

    completed = client.get(f"/api/runs/{run_id}/jobs?job_status=job_complete").json()
    assert len(completed) == 1

    failed = client.get(f"/api/runs/{run_id}/jobs?job_status=failed").json()
    assert len(failed) == 1


# ── Get job ────────────────────────────────────────────────────────────────────


def test_get_job_returns_detail(client):
    _, plan_id, run_id = _setup_run(client)
    job_ids = _seed_jobs(plan_id, run_id, n=1)
    resp = client.get(f"/api/jobs/{job_ids[0]}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == job_ids[0]
    assert body["records_processed"] == 100


def test_get_job_not_found_returns_404(client):
    assert client.get("/api/jobs/nonexistent").status_code == 404


# ── Download CSVs ──────────────────────────────────────────────────────────────


def _seed_job_with_files(plan_id: str, run_id: str, success: str = None, error: str = None) -> str:
    from app.models.job import JobRecord, JobStatus
    from app.models.load_step import LoadStep, Operation
    from tests.conftest import _TestSession, _run_async

    job_id: list[str] = []

    async def _insert():
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
            job = JobRecord(
                load_run_id=run_id,
                load_step_id=step.id,
                partition_index=0,
                status=JobStatus.job_complete,
                success_file_path=success,
                error_file_path=error,
            )
            session.add(job)
            await session.flush()
            job_id.append(job.id)
            await session.commit()

    _run_async(_insert())
    return job_id[0]


def test_download_success_csv(client):
    _, plan_id, run_id = _setup_run(client)

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_rel = "success.csv"
        csv_path = os.path.join(tmpdir, csv_rel)
        with open(csv_path, "w") as f:
            f.write("id,sf__Id\n1,0011x000001\n")

        job_id = _seed_job_with_files(plan_id, run_id, success=csv_rel)

        from unittest.mock import patch
        with patch("app.api.jobs.settings") as mock_settings:
            mock_settings.output_dir = tmpdir
            resp = client.get(f"/api/jobs/{job_id}/success-csv")

    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]


def test_download_success_csv_not_available_returns_404(client):
    _, plan_id, run_id = _setup_run(client)
    job_id = _seed_job_with_files(plan_id, run_id)  # no file paths
    resp = client.get(f"/api/jobs/{job_id}/success-csv")
    assert resp.status_code == 404


def test_download_error_csv_not_available_returns_404(client):
    _, plan_id, run_id = _setup_run(client)
    job_id = _seed_job_with_files(plan_id, run_id)
    resp = client.get(f"/api/jobs/{job_id}/error-csv")
    assert resp.status_code == 404


def test_download_unprocessed_csv_not_available_returns_404(client):
    _, plan_id, run_id = _setup_run(client)
    job_id = _seed_job_with_files(plan_id, run_id)
    resp = client.get(f"/api/jobs/{job_id}/unprocessed-csv")
    assert resp.status_code == 404


def test_download_error_csv_file_missing_on_disk_returns_404(client):
    _, plan_id, run_id = _setup_run(client)
    # Record exists in DB but file is gone from disk
    job_id = _seed_job_with_files(plan_id, run_id, error="missing_file.csv")

    from unittest.mock import patch
    with patch("app.api.jobs.settings") as mock_settings:
        mock_settings.output_dir = "/nonexistent"
        resp = client.get(f"/api/jobs/{job_id}/error-csv")

    assert resp.status_code == 404
