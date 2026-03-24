"""Tests for the /api/runs/{run_id}/jobs and /api/jobs/{id} endpoints."""

import csv
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


def _setup_run(auth_client) -> tuple[str, str, str]:
    """Create connection + plan + run; return (conn_id, plan_id, run_id)."""
    conn_id = auth_client.post("/api/connections/", json=_CONN).json()["id"]
    plan_id = auth_client.post(
        "/api/load-plans/",
        json={"name": "Plan", "connection_id": conn_id},
    ).json()["id"]
    run_id = auth_client.post(f"/api/load-plans/{plan_id}/run").json()["id"]
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


def test_list_jobs_empty(auth_client):
    _, _, run_id = _setup_run(auth_client)
    resp = auth_client.get(f"/api/runs/{run_id}/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_jobs_returns_jobs(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)
    _seed_jobs(plan_id, run_id, n=3)
    resp = auth_client.get(f"/api/runs/{run_id}/jobs")
    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_list_jobs_filter_by_status(auth_client):
    from app.models.job import JobStatus
    from app.models.load_step import LoadStep, Operation
    from tests.conftest import _TestSession, _run_async

    _, plan_id, run_id = _setup_run(auth_client)

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

    completed = auth_client.get(f"/api/runs/{run_id}/jobs?job_status=job_complete").json()
    assert len(completed) == 1

    failed = auth_client.get(f"/api/runs/{run_id}/jobs?job_status=failed").json()
    assert len(failed) == 1


# ── Get job ────────────────────────────────────────────────────────────────────


def test_get_job_returns_detail(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)
    job_ids = _seed_jobs(plan_id, run_id, n=1)
    resp = auth_client.get(f"/api/jobs/{job_ids[0]}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == job_ids[0]
    assert body["records_processed"] == 100


def test_get_job_not_found_returns_404(auth_client):
    assert auth_client.get("/api/jobs/nonexistent").status_code == 404


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


def test_download_success_csv(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_rel = "success.csv"
        csv_path = os.path.join(tmpdir, csv_rel)
        with open(csv_path, "w") as f:
            f.write("id,sf__Id\n1,0011x000001\n")

        job_id = _seed_job_with_files(plan_id, run_id, success=csv_rel)

        from unittest.mock import patch
        with patch("app.api.jobs.settings") as mock_settings:
            mock_settings.output_dir = tmpdir
            resp = auth_client.get(f"/api/jobs/{job_id}/success-csv")

    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]


def test_download_success_csv_not_available_returns_404(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)
    job_id = _seed_job_with_files(plan_id, run_id)  # no file paths
    resp = auth_client.get(f"/api/jobs/{job_id}/success-csv")
    assert resp.status_code == 404


def test_download_error_csv_not_available_returns_404(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)
    job_id = _seed_job_with_files(plan_id, run_id)
    resp = auth_client.get(f"/api/jobs/{job_id}/error-csv")
    assert resp.status_code == 404


def test_download_unprocessed_csv_not_available_returns_404(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)
    job_id = _seed_job_with_files(plan_id, run_id)
    resp = auth_client.get(f"/api/jobs/{job_id}/unprocessed-csv")
    assert resp.status_code == 404


def test_download_error_csv_file_missing_on_disk_returns_404(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)
    # Record exists in DB but file is gone from disk
    job_id = _seed_job_with_files(plan_id, run_id, error="missing_file.csv")

    from unittest.mock import patch
    with patch("app.api.jobs.settings") as mock_settings:
        mock_settings.output_dir = "/nonexistent"
        resp = auth_client.get(f"/api/jobs/{job_id}/error-csv")

    assert resp.status_code == 404


def _seed_job_with_unprocessed(plan_id: str, run_id: str, unprocessed: str = None) -> str:
    """Seed a job with an unprocessed file path."""
    from app.models.job import JobRecord, JobStatus
    from app.models.load_step import LoadStep, Operation
    from tests.conftest import _TestSession, _run_async

    job_id: list[str] = []

    async def _insert():
        async with _TestSession() as session:
            step = LoadStep(
                load_plan_id=plan_id,
                sequence=2,
                object_name="Contact",
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
                unprocessed_file_path=unprocessed,
            )
            session.add(job)
            await session.flush()
            job_id.append(job.id)
            await session.commit()

    _run_async(_insert())
    return job_id[0]


def _write_csv(path, header, data_rows):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in data_rows:
            writer.writerow(row)


# ── Preview CSVs ───────────────────────────────────────────────────────────────


def test_preview_success_csv_returns_header_and_rows(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_csv(
            os.path.join(tmpdir, "success.csv"),
            ["sf__Id", "sf__Created"],
            [{"sf__Id": f"00{i}", "sf__Created": "true"} for i in range(3)],
        )
        job_id = _seed_job_with_files(plan_id, run_id, success="success.csv")

        from unittest.mock import patch
        with patch("app.api.jobs.settings") as mock_settings:
            mock_settings.output_dir = tmpdir
            resp = auth_client.get(f"/api/jobs/{job_id}/success-csv/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["header"] == ["sf__Id", "sf__Created"]
    assert len(body["rows"]) == 3
    assert body["has_next"] is False
    assert body["offset"] == 0
    assert body["limit"] == 50  # default
    assert body["total_rows"] is None
    assert body["filtered_rows"] is None


def test_preview_success_csv_has_next_true(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_csv(
            os.path.join(tmpdir, "success.csv"),
            ["sf__Id"],
            [{"sf__Id": str(i)} for i in range(5)],
        )
        job_id = _seed_job_with_files(plan_id, run_id, success="success.csv")

        from unittest.mock import patch
        with patch("app.api.jobs.settings") as mock_settings:
            mock_settings.output_dir = tmpdir
            resp = auth_client.get(f"/api/jobs/{job_id}/success-csv/preview?limit=2")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 2
    assert body["has_next"] is True
    assert body["limit"] == 2


def test_preview_success_csv_has_next_false_on_last_page(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_csv(
            os.path.join(tmpdir, "success.csv"),
            ["sf__Id"],
            [{"sf__Id": str(i)} for i in range(3)],
        )
        job_id = _seed_job_with_files(plan_id, run_id, success="success.csv")

        from unittest.mock import patch
        with patch("app.api.jobs.settings") as mock_settings:
            mock_settings.output_dir = tmpdir
            resp = auth_client.get(f"/api/jobs/{job_id}/success-csv/preview?limit=5")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 3
    assert body["has_next"] is False


def test_preview_success_csv_offset_returns_second_page(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_csv(
            os.path.join(tmpdir, "success.csv"),
            ["sf__Id"],
            [{"sf__Id": f"row{i}"} for i in range(4)],
        )
        job_id = _seed_job_with_files(plan_id, run_id, success="success.csv")

        from unittest.mock import patch
        with patch("app.api.jobs.settings") as mock_settings:
            mock_settings.output_dir = tmpdir
            resp = auth_client.get(f"/api/jobs/{job_id}/success-csv/preview?limit=2&offset=2")

    assert resp.status_code == 200
    body = resp.json()
    assert body["offset"] == 2
    assert body["limit"] == 2
    assert len(body["rows"]) == 2
    assert body["rows"][0]["sf__Id"] == "row2"
    assert body["rows"][1]["sf__Id"] == "row3"
    assert body["has_next"] is False


def test_preview_success_csv_filtered_returns_filtered_rows(auth_client):
    import urllib.parse

    _, plan_id, run_id = _setup_run(auth_client)

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_csv(
            os.path.join(tmpdir, "success.csv"),
            ["sf__Id", "sf__Error"],
            [
                {"sf__Id": "001", "sf__Error": ""},
                {"sf__Id": "002", "sf__Error": "DUPLICATE_VALUE"},
                {"sf__Id": "003", "sf__Error": "DUPLICATE_VALUE"},
                {"sf__Id": "004", "sf__Error": ""},
            ],
        )
        job_id = _seed_job_with_files(plan_id, run_id, success="success.csv")

        filters_json = urllib.parse.quote('[{"column":"sf__Error","value":"DUPLICATE"}]')
        from unittest.mock import patch
        with patch("app.api.jobs.settings") as mock_settings:
            mock_settings.output_dir = tmpdir
            resp = auth_client.get(
                f"/api/jobs/{job_id}/success-csv/preview?filters={filters_json}"
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["filtered_rows"] == 2
    assert len(body["rows"]) == 2
    assert all("DUPLICATE" in row["sf__Error"] for row in body["rows"])
    assert body["has_next"] is False


def test_preview_success_csv_filtered_no_matches(auth_client):
    import urllib.parse

    _, plan_id, run_id = _setup_run(auth_client)

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_csv(
            os.path.join(tmpdir, "success.csv"),
            ["sf__Id"],
            [{"sf__Id": str(i)} for i in range(3)],
        )
        job_id = _seed_job_with_files(plan_id, run_id, success="success.csv")

        filters_json = urllib.parse.quote('[{"column":"sf__Id","value":"NOMATCH"}]')
        from unittest.mock import patch
        with patch("app.api.jobs.settings") as mock_settings:
            mock_settings.output_dir = tmpdir
            resp = auth_client.get(
                f"/api/jobs/{job_id}/success-csv/preview?filters={filters_json}"
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"] == []
    assert body["filtered_rows"] == 0
    assert body["has_next"] is False


def test_preview_success_csv_no_path_returns_404(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)
    job_id = _seed_job_with_files(plan_id, run_id)  # no success path
    resp = auth_client.get(f"/api/jobs/{job_id}/success-csv/preview")
    assert resp.status_code == 404


def test_preview_success_csv_file_missing_on_disk_returns_404(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)
    job_id = _seed_job_with_files(plan_id, run_id, success="missing.csv")

    from unittest.mock import patch
    with patch("app.api.jobs.settings") as mock_settings:
        mock_settings.output_dir = "/nonexistent"
        resp = auth_client.get(f"/api/jobs/{job_id}/success-csv/preview")

    assert resp.status_code == 404


def test_preview_success_csv_unknown_filter_column_returns_400(auth_client):
    import urllib.parse

    _, plan_id, run_id = _setup_run(auth_client)

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_csv(os.path.join(tmpdir, "success.csv"), ["sf__Id"], [{"sf__Id": "1"}])
        job_id = _seed_job_with_files(plan_id, run_id, success="success.csv")

        filters_json = urllib.parse.quote('[{"column":"Nonexistent","value":"x"}]')
        from unittest.mock import patch
        with patch("app.api.jobs.settings") as mock_settings:
            mock_settings.output_dir = tmpdir
            resp = auth_client.get(
                f"/api/jobs/{job_id}/success-csv/preview?filters={filters_json}"
            )

    assert resp.status_code == 400


def test_preview_success_csv_malformed_filters_json_returns_400(auth_client):
    import urllib.parse

    _, plan_id, run_id = _setup_run(auth_client)

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_csv(os.path.join(tmpdir, "success.csv"), ["sf__Id"], [{"sf__Id": "1"}])
        job_id = _seed_job_with_files(plan_id, run_id, success="success.csv")

        bad = urllib.parse.quote("not-json")
        from unittest.mock import patch
        with patch("app.api.jobs.settings") as mock_settings:
            mock_settings.output_dir = tmpdir
            resp = auth_client.get(f"/api/jobs/{job_id}/success-csv/preview?filters={bad}")

    assert resp.status_code == 400
    assert "Invalid filters JSON" in resp.json()["detail"]


def test_preview_error_csv_returns_header_and_rows(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_csv(
            os.path.join(tmpdir, "error.csv"),
            ["sf__Id", "sf__Error"],
            [{"sf__Id": "001", "sf__Error": "REQUIRED_FIELD_MISSING"}],
        )
        job_id = _seed_job_with_files(plan_id, run_id, error="error.csv")

        from unittest.mock import patch
        with patch("app.api.jobs.settings") as mock_settings:
            mock_settings.output_dir = tmpdir
            resp = auth_client.get(f"/api/jobs/{job_id}/error-csv/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["header"] == ["sf__Id", "sf__Error"]
    assert len(body["rows"]) == 1
    assert "has_next" in body
    assert "limit" in body


def test_preview_unprocessed_csv_returns_header_and_rows(auth_client):
    _, plan_id, run_id = _setup_run(auth_client)

    with tempfile.TemporaryDirectory() as tmpdir:
        _write_csv(
            os.path.join(tmpdir, "unprocessed.csv"),
            ["Name", "Industry"],
            [{"Name": "Acme", "Industry": "Tech"}],
        )
        job_id = _seed_job_with_unprocessed(plan_id, run_id, unprocessed="unprocessed.csv")

        from unittest.mock import patch
        with patch("app.api.jobs.settings") as mock_settings:
            mock_settings.output_dir = tmpdir
            resp = auth_client.get(f"/api/jobs/{job_id}/unprocessed-csv/preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["header"] == ["Name", "Industry"]
    assert len(body["rows"]) == 1
    assert "has_next" in body
