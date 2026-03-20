"""Tests for GET /runs/{id}/logs.zip endpoint and build_logs_zip service."""

import asyncio
import io
import zipfile
from unittest.mock import patch

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


def _setup(auth_client) -> tuple[str, str, str, str]:
    """Create connection + plan + step; return (conn_id, plan_id, step_id, run_id)."""
    conn_id = auth_client.post("/api/connections/", json=_CONN).json()["id"]
    plan_id = auth_client.post(
        "/api/load-plans/",
        json={"name": "Test Plan", "connection_id": conn_id},
    ).json()["id"]
    step_id = auth_client.post(
        f"/api/load-plans/{plan_id}/steps", json=_STEP
    ).json()["id"]
    run_id = _seed_run(plan_id)
    return conn_id, plan_id, step_id, run_id


def _seed_run(plan_id: str) -> str:
    async def _create():
        async with _S() as db:
            run = LoadRun(
                load_plan_id=plan_id, status=RunStatus.completed, initiated_by="test"
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)
            return run.id

    return _run(_create())


def _seed_job(
    run_id: str,
    step_id: str,
    *,
    partition_index: int = 0,
    success_file_path: str | None = None,
    error_file_path: str | None = None,
    unprocessed_file_path: str | None = None,
) -> str:
    async def _create():
        async with _S() as db:
            job = JobRecord(
                load_run_id=run_id,
                load_step_id=step_id,
                partition_index=partition_index,
                status=JobStatus.job_complete,
                success_file_path=success_file_path,
                error_file_path=error_file_path,
                unprocessed_file_path=unprocessed_file_path,
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            return job.id

    return _run(_create())


def _write_csv(tmp_path, rel: str, content: str = "Name\nAlice\n") -> None:
    full = tmp_path / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def _namelist(resp) -> list[str]:
    return zipfile.ZipFile(io.BytesIO(resp.content)).namelist()


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_logs_zip_returns_200_with_zip_content_type(auth_client, tmp_path):
    _, plan_id, step_id, run_id = _setup(auth_client)
    _seed_job(run_id, step_id)  # no files → empty archive

    with patch("app.services.load_run_service.settings") as mock_s:
        mock_s.output_dir = str(tmp_path)
        resp = auth_client.get(f"/api/runs/{run_id}/logs.zip")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"


def test_logs_zip_contains_expected_files_in_archive(auth_client, tmp_path):
    _, plan_id, step_id, run_id = _setup(auth_client)

    success_rel = f"{run_id}/{step_id}/partition_0_success.csv"
    error_rel = f"{run_id}/{step_id}/partition_0_error.csv"
    unprocessed_rel = f"{run_id}/{step_id}/partition_0_unprocessed.csv"
    for rel in (success_rel, error_rel, unprocessed_rel):
        _write_csv(tmp_path, rel)

    _seed_job(
        run_id, step_id,
        success_file_path=success_rel,
        error_file_path=error_rel,
        unprocessed_file_path=unprocessed_rel,
    )

    with patch("app.services.load_run_service.settings") as mock_s:
        mock_s.output_dir = str(tmp_path)
        resp = auth_client.get(f"/api/runs/{run_id}/logs.zip")

    names = _namelist(resp)
    # First component (run_id) is stripped → step_id prefixed paths
    assert f"{step_id}/partition_0_success.csv" in names
    assert f"{step_id}/partition_0_error.csv" in names
    assert f"{step_id}/partition_0_unprocessed.csv" in names


def test_logs_zip_success_false_excludes_success_files(auth_client, tmp_path):
    _, plan_id, step_id, run_id = _setup(auth_client)

    success_rel = f"{run_id}/{step_id}/partition_0_success.csv"
    error_rel = f"{run_id}/{step_id}/partition_0_error.csv"
    unprocessed_rel = f"{run_id}/{step_id}/partition_0_unprocessed.csv"
    for rel in (success_rel, error_rel, unprocessed_rel):
        _write_csv(tmp_path, rel)

    _seed_job(
        run_id, step_id,
        success_file_path=success_rel,
        error_file_path=error_rel,
        unprocessed_file_path=unprocessed_rel,
    )

    with patch("app.services.load_run_service.settings") as mock_s:
        mock_s.output_dir = str(tmp_path)
        resp = auth_client.get(f"/api/runs/{run_id}/logs.zip?success=false")

    names = _namelist(resp)
    assert not any("success" in n for n in names)
    assert f"{step_id}/partition_0_error.csv" in names
    assert f"{step_id}/partition_0_unprocessed.csv" in names


def test_logs_zip_errors_false_excludes_error_files(auth_client, tmp_path):
    _, plan_id, step_id, run_id = _setup(auth_client)

    error_rel = f"{run_id}/{step_id}/partition_0_error.csv"
    _write_csv(tmp_path, error_rel)
    _seed_job(run_id, step_id, error_file_path=error_rel)

    with patch("app.services.load_run_service.settings") as mock_s:
        mock_s.output_dir = str(tmp_path)
        resp = auth_client.get(f"/api/runs/{run_id}/logs.zip?errors=false")

    names = _namelist(resp)
    assert not any("error" in n for n in names)


def test_logs_zip_unprocessed_false_excludes_unprocessed_files(auth_client, tmp_path):
    _, plan_id, step_id, run_id = _setup(auth_client)

    unprocessed_rel = f"{run_id}/{step_id}/partition_0_unprocessed.csv"
    _write_csv(tmp_path, unprocessed_rel)
    _seed_job(run_id, step_id, unprocessed_file_path=unprocessed_rel)

    with patch("app.services.load_run_service.settings") as mock_s:
        mock_s.output_dir = str(tmp_path)
        resp = auth_client.get(f"/api/runs/{run_id}/logs.zip?unprocessed=false")

    names = _namelist(resp)
    assert not any("unprocessed" in n for n in names)


def test_logs_zip_all_false_returns_empty_zip(auth_client, tmp_path):
    _, plan_id, step_id, run_id = _setup(auth_client)

    success_rel = f"{run_id}/{step_id}/partition_0_success.csv"
    _write_csv(tmp_path, success_rel)
    _seed_job(run_id, step_id, success_file_path=success_rel)

    with patch("app.services.load_run_service.settings") as mock_s:
        mock_s.output_dir = str(tmp_path)
        resp = auth_client.get(
            f"/api/runs/{run_id}/logs.zip?success=false&errors=false&unprocessed=false"
        )

    assert resp.status_code == 200
    assert _namelist(resp) == []


def test_logs_zip_missing_files_silently_skipped(auth_client, tmp_path):
    _, plan_id, step_id, run_id = _setup(auth_client)

    success_rel = f"{run_id}/{step_id}/partition_0_success.csv"
    _write_csv(tmp_path, success_rel)
    # error_rel path points to a nonexistent file
    error_rel = f"{run_id}/{step_id}/partition_0_error.csv"

    _seed_job(
        run_id, step_id,
        success_file_path=success_rel,
        error_file_path=error_rel,  # file does not exist on disk
    )

    with patch("app.services.load_run_service.settings") as mock_s:
        mock_s.output_dir = str(tmp_path)
        resp = auth_client.get(f"/api/runs/{run_id}/logs.zip")

    assert resp.status_code == 200
    names = _namelist(resp)
    assert f"{step_id}/partition_0_success.csv" in names
    assert not any("error" in n for n in names)


def test_logs_zip_null_file_paths_silently_skipped(auth_client, tmp_path):
    _, plan_id, step_id, run_id = _setup(auth_client)
    # All file paths are NULL
    _seed_job(run_id, step_id)

    with patch("app.services.load_run_service.settings") as mock_s:
        mock_s.output_dir = str(tmp_path)
        resp = auth_client.get(f"/api/runs/{run_id}/logs.zip")

    assert resp.status_code == 200
    assert _namelist(resp) == []


def test_logs_zip_multi_step_archive_structure(auth_client, tmp_path):
    conn_id = auth_client.post("/api/connections/", json=_CONN).json()["id"]
    plan_id = auth_client.post(
        "/api/load-plans/",
        json={"name": "Multi-step Plan", "connection_id": conn_id},
    ).json()["id"]
    step_a_id = auth_client.post(
        f"/api/load-plans/{plan_id}/steps",
        json={**_STEP, "sequence": 1, "object_name": "Account"},
    ).json()["id"]
    step_b_id = auth_client.post(
        f"/api/load-plans/{plan_id}/steps",
        json={**_STEP, "sequence": 2, "object_name": "Contact"},
    ).json()["id"]
    run_id = _seed_run(plan_id)

    error_rel_a = f"{run_id}/{step_a_id}/partition_0_error.csv"
    error_rel_b = f"{run_id}/{step_b_id}/partition_0_error.csv"
    _write_csv(tmp_path, error_rel_a)
    _write_csv(tmp_path, error_rel_b)

    _seed_job(run_id, step_a_id, error_file_path=error_rel_a)
    _seed_job(run_id, step_b_id, error_file_path=error_rel_b)

    with patch("app.services.load_run_service.settings") as mock_s:
        mock_s.output_dir = str(tmp_path)
        resp = auth_client.get(
            f"/api/runs/{run_id}/logs.zip?success=false&unprocessed=false"
        )

    names = _namelist(resp)
    assert f"{step_a_id}/partition_0_error.csv" in names
    assert f"{step_b_id}/partition_0_error.csv" in names
    assert len(names) == 2


def test_logs_zip_run_not_found_returns_404(auth_client):
    resp = auth_client.get("/api/runs/nonexistent/logs.zip")
    assert resp.status_code == 404


def test_logs_zip_requires_authentication(client):
    """Unauthenticated request should be rejected."""
    resp = client.get("/api/runs/any-run-id/logs.zip")
    assert resp.status_code in (401, 403)
