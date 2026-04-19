"""Tests for result_persistence.download_and_persist_results (SFBL-161).

Covers:
- LocalOutputStorage: files written to expected path, job record paths set correctly.
- S3OutputStorage (stub): job record paths are s3:// URIs.
- OutputStorageError during write: caught and logged; function returns without raising;
  file path field remains None.
- BulkAPIError during download: existing behaviour unchanged.
"""

from __future__ import annotations

import io
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.models.job import JobRecord, JobStatus
from app.services.output_storage import (
    LocalOutputStorage,
    OutputStorageError,
    S3OutputStorage,
)
from app.services.result_persistence import download_and_persist_results
from app.services.salesforce_bulk import BulkAPIError


# ── Fixtures and helpers ──────────────────────────────────────────────────────


def _make_job_record(partition_index: int = 0) -> JobRecord:
    jr = JobRecord(
        load_run_id="run-001",
        load_step_id="step-001",
        partition_index=partition_index,
        status=JobStatus.in_progress,
        total_records=2,
    )
    return jr


CSV_SUCCESS = b"sf__Id,sf__Created,Name\naaa,true,Acme\nbbb,false,Beta\n"
CSV_ERRORS = b"sf__Id,sf__Error,Name\n,REQUIRED_FIELD_MISSING,Bad Corp\n"
CSV_UNPROCESSED = b"Name,ExternalId__c\nSkipped,EXT-999\n"
CSV_HEADER_ONLY = b"sf__Id,sf__Created\n"


def _make_bulk_client(
    *,
    success_csv: bytes = CSV_SUCCESS,
    error_csv: bytes = CSV_HEADER_ONLY,
    unprocessed_csv: bytes = CSV_HEADER_ONLY,
    success_exc: Exception | None = None,
    error_exc: Exception | None = None,
    unprocessed_exc: Exception | None = None,
) -> MagicMock:
    client = MagicMock()
    if success_exc:
        client.get_success_results = AsyncMock(side_effect=success_exc)
    else:
        client.get_success_results = AsyncMock(return_value=success_csv)
    if error_exc:
        client.get_failed_results = AsyncMock(side_effect=error_exc)
    else:
        client.get_failed_results = AsyncMock(return_value=error_csv)
    if unprocessed_exc:
        client.get_unprocessed_results = AsyncMock(side_effect=unprocessed_exc)
    else:
        client.get_unprocessed_results = AsyncMock(return_value=unprocessed_csv)
    return client


# ── LocalOutputStorage tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_storage_success_file_written(tmp_path):
    """LocalOutputStorage: success CSV is written to the correct path."""
    storage = LocalOutputStorage(str(tmp_path))
    jr = _make_job_record(partition_index=0)
    client = _make_bulk_client(success_csv=CSV_SUCCESS)

    await download_and_persist_results(
        bulk_client=client,
        sf_job_id="JOB001",
        job_record=jr,
        run_id="run-001",
        plan_id="plan-001-fixture",
        plan_name="Test Plan",
        step_sequence=1,
        object_name="Account",
        operation="insert",
        output_storage=storage,
    )

    expected = (
        tmp_path
        / "plan-001-test-plan"
        / "run-001"
        / "01_account_insert"
        / "partition_0_success.csv"
    )
    assert expected.exists()
    assert expected.read_bytes() == CSV_SUCCESS


@pytest.mark.asyncio
async def test_local_storage_job_record_success_path(tmp_path):
    """LocalOutputStorage: job_record.success_file_path is the relative path."""
    storage = LocalOutputStorage(str(tmp_path))
    jr = _make_job_record(partition_index=0)
    client = _make_bulk_client(success_csv=CSV_SUCCESS)

    await download_and_persist_results(
        bulk_client=client,
        sf_job_id="JOB001",
        job_record=jr,
        run_id="run-001",
        plan_id="plan-001-fixture",
        plan_name="Test Plan",
        step_sequence=1,
        object_name="Account",
        operation="insert",
        output_storage=storage,
    )

    assert jr.success_file_path == (
        "plan-001-test-plan/run-001/01_account_insert/partition_0_success.csv"
    )


@pytest.mark.asyncio
async def test_local_storage_error_file_written(tmp_path):
    """LocalOutputStorage: error CSV is written and error_file_path is set."""
    storage = LocalOutputStorage(str(tmp_path))
    jr = _make_job_record(partition_index=1)
    client = _make_bulk_client(success_csv=CSV_HEADER_ONLY, error_csv=CSV_ERRORS)

    await download_and_persist_results(
        bulk_client=client,
        sf_job_id="JOB002",
        job_record=jr,
        run_id="run-001",
        plan_id="plan-001-fixture",
        plan_name="Test Plan",
        step_sequence=1,
        object_name="Account",
        operation="insert",
        output_storage=storage,
    )

    assert jr.error_file_path == (
        "plan-001-test-plan/run-001/01_account_insert/partition_1_errors.csv"
    )
    expected = (
        tmp_path
        / "plan-001-test-plan"
        / "run-001"
        / "01_account_insert"
        / "partition_1_errors.csv"
    )
    assert expected.read_bytes() == CSV_ERRORS


@pytest.mark.asyncio
async def test_local_storage_unprocessed_file_written(tmp_path):
    """LocalOutputStorage: unprocessed CSV is written and unprocessed_file_path is set."""
    storage = LocalOutputStorage(str(tmp_path))
    jr = _make_job_record(partition_index=2)
    client = _make_bulk_client(
        success_csv=CSV_HEADER_ONLY,
        error_csv=CSV_HEADER_ONLY,
        unprocessed_csv=CSV_UNPROCESSED,
    )

    await download_and_persist_results(
        bulk_client=client,
        sf_job_id="JOB003",
        job_record=jr,
        run_id="run-001",
        plan_id="plan-001-fixture",
        plan_name="Test Plan",
        step_sequence=1,
        object_name="Account",
        operation="insert",
        output_storage=storage,
    )

    assert jr.unprocessed_file_path == (
        "plan-001-test-plan/run-001/01_account_insert/partition_2_unprocessed.csv"
    )
    expected = (
        tmp_path
        / "plan-001-test-plan"
        / "run-001"
        / "01_account_insert"
        / "partition_2_unprocessed.csv"
    )
    assert expected.read_bytes() == CSV_UNPROCESSED


@pytest.mark.asyncio
async def test_local_storage_returns_correct_counts(tmp_path):
    """LocalOutputStorage: records_processed and records_failed are correct."""
    storage = LocalOutputStorage(str(tmp_path))
    jr = _make_job_record()
    client = _make_bulk_client(success_csv=CSV_SUCCESS, error_csv=CSV_ERRORS)

    processed, failed = await download_and_persist_results(
        bulk_client=client,
        sf_job_id="JOB001",
        job_record=jr,
        run_id="run-001",
        plan_id="plan-001-fixture",
        plan_name="Test Plan",
        step_sequence=1,
        object_name="Account",
        operation="insert",
        output_storage=storage,
    )

    assert failed == 1
    assert processed == 3  # 2 success + 1 error


@pytest.mark.asyncio
async def test_local_storage_paths_not_set_when_empty_bytes(tmp_path):
    """Empty bytes from Salesforce (falsy) → no file written, path remains None."""
    storage = LocalOutputStorage(str(tmp_path))
    jr = _make_job_record()
    # Empty bytes (b"") is falsy — this is what Salesforce returns when no results exist.
    client = _make_bulk_client(
        success_csv=b"",
        error_csv=b"",
        unprocessed_csv=b"",
    )

    await download_and_persist_results(
        bulk_client=client,
        sf_job_id="JOB001",
        job_record=jr,
        run_id="run-001",
        plan_id="plan-001-fixture",
        plan_name="Test Plan",
        step_sequence=1,
        object_name="Account",
        operation="insert",
        output_storage=storage,
    )

    assert jr.success_file_path is None
    assert jr.error_file_path is None
    assert jr.unprocessed_file_path is None


# ── S3OutputStorage (stub) tests ──────────────────────────────────────────────


class _FakeS3Client:
    """Minimal boto3 S3 stub that records upload_fileobj calls."""

    def __init__(self) -> None:
        self.uploads: dict[str, bytes] = {}

    def upload_fileobj(self, fileobj: io.IOBase, bucket: str, key: str, **_kwargs) -> None:
        self.uploads[key] = fileobj.read()


def _make_s3_storage(fake_client, *, bucket: str = "output-bucket", root_prefix=None):
    with patch("app.services.output_storage.boto3.client", return_value=fake_client):
        return S3OutputStorage(
            bucket=bucket,
            root_prefix=root_prefix,
            region="us-east-1",
            access_key_id="AKID",
            secret_access_key="SAK",
        )


@pytest.mark.asyncio
async def test_s3_storage_job_record_paths_are_s3_uris():
    """S3OutputStorage: job_record.*_file_path values are s3:// URIs."""
    fake = _FakeS3Client()
    storage = _make_s3_storage(fake, bucket="my-bucket")
    jr = _make_job_record(partition_index=0)
    client = _make_bulk_client(
        success_csv=CSV_SUCCESS,
        error_csv=CSV_ERRORS,
        unprocessed_csv=CSV_UNPROCESSED,
    )

    await download_and_persist_results(
        bulk_client=client,
        sf_job_id="JOB001",
        job_record=jr,
        run_id="run-001",
        plan_id="plan-001-fixture",
        plan_name="Test Plan",
        step_sequence=1,
        object_name="Account",
        operation="insert",
        output_storage=storage,
    )

    assert jr.success_file_path and jr.success_file_path.startswith("s3://my-bucket/")
    assert jr.error_file_path and jr.error_file_path.startswith("s3://my-bucket/")
    assert jr.unprocessed_file_path and jr.unprocessed_file_path.startswith("s3://my-bucket/")


@pytest.mark.asyncio
async def test_s3_storage_success_uri_contains_correct_key():
    """S3OutputStorage: success URI contains run_id/step_id/partition pattern."""
    fake = _FakeS3Client()
    storage = _make_s3_storage(fake, bucket="my-bucket")
    jr = _make_job_record(partition_index=3)
    client = _make_bulk_client(success_csv=CSV_SUCCESS)

    await download_and_persist_results(
        bulk_client=client,
        sf_job_id="JOB001",
        job_record=jr,
        run_id="run-abc",
        plan_id="plan-001-fixture",
        plan_name="Test Plan",
        step_sequence=2,
        object_name="Contact",
        operation="update",
        output_storage=storage,
    )

    assert jr.success_file_path == (
        "s3://my-bucket/plan-001-test-plan/run-abc/02_contact_update/partition_3_success.csv"
    )


@pytest.mark.asyncio
async def test_s3_storage_data_uploaded_correctly():
    """S3OutputStorage: bytes uploaded to S3 match the CSV bytes."""
    fake = _FakeS3Client()
    storage = _make_s3_storage(fake, bucket="b")
    jr = _make_job_record(partition_index=0)
    client = _make_bulk_client(success_csv=CSV_SUCCESS, error_csv=CSV_HEADER_ONLY)

    await download_and_persist_results(
        bulk_client=client,
        sf_job_id="JOB001",
        job_record=jr,
        run_id="run-001",
        plan_id="plan-001-fixture",
        plan_name="Test Plan",
        step_sequence=1,
        object_name="Account",
        operation="insert",
        output_storage=storage,
    )

    key = "plan-001-test-plan/run-001/01_account_insert/partition_0_success.csv"
    assert key in fake.uploads
    assert fake.uploads[key] == CSV_SUCCESS


# ── OutputStorageError handling ───────────────────────────────────────────────


class _ErroringStorage:
    """Output storage stub that always raises OutputStorageError."""

    def write_bytes(self, relative_path: str, data: bytes) -> str:
        raise OutputStorageError("simulated write failure")


@pytest.mark.asyncio
async def test_output_storage_error_is_caught_does_not_raise():
    """OutputStorageError during write is caught; function returns without raising."""
    storage = _ErroringStorage()
    jr = _make_job_record()
    client = _make_bulk_client(
        success_csv=CSV_SUCCESS,
        error_csv=CSV_ERRORS,
        unprocessed_csv=CSV_UNPROCESSED,
    )

    # Must not raise
    result = await download_and_persist_results(
        bulk_client=client,
        sf_job_id="JOB001",
        job_record=jr,
        run_id="run-001",
        plan_id="plan-001-fixture",
        plan_name="Test Plan",
        step_sequence=1,
        object_name="Account",
        operation="insert",
        output_storage=storage,
    )
    assert isinstance(result, tuple)


@pytest.mark.asyncio
async def test_output_storage_error_file_path_remains_none():
    """When write fails, job_record.*_file_path fields remain None."""
    storage = _ErroringStorage()
    jr = _make_job_record()
    client = _make_bulk_client(
        success_csv=CSV_SUCCESS,
        error_csv=CSV_ERRORS,
        unprocessed_csv=CSV_UNPROCESSED,
    )

    await download_and_persist_results(
        bulk_client=client,
        sf_job_id="JOB001",
        job_record=jr,
        run_id="run-001",
        plan_id="plan-001-fixture",
        plan_name="Test Plan",
        step_sequence=1,
        object_name="Account",
        operation="insert",
        output_storage=storage,
    )

    assert jr.success_file_path is None
    assert jr.error_file_path is None
    assert jr.unprocessed_file_path is None


@pytest.mark.asyncio
async def test_output_storage_error_logged_as_warning(caplog):
    """OutputStorageError is logged at WARNING level."""
    import logging

    storage = _ErroringStorage()
    jr = _make_job_record()
    client = _make_bulk_client(success_csv=CSV_SUCCESS)

    with caplog.at_level(logging.WARNING, logger="app.services.result_persistence"):
        await download_and_persist_results(
            bulk_client=client,
            sf_job_id="JOB001",
            job_record=jr,
            run_id="run-001",
            plan_id="plan-001-fixture",
            plan_name="Test Plan",
            step_sequence=1,
            object_name="Account",
            operation="insert",
            output_storage=storage,
        )

    assert any("simulated write failure" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_output_storage_error_row_counts_still_returned():
    """Even when write fails, row counts are still computed and returned."""
    storage = _ErroringStorage()
    jr = _make_job_record()
    client = _make_bulk_client(success_csv=CSV_SUCCESS, error_csv=CSV_ERRORS)

    processed, failed = await download_and_persist_results(
        bulk_client=client,
        sf_job_id="JOB001",
        job_record=jr,
        run_id="run-001",
        plan_id="plan-001-fixture",
        plan_name="Test Plan",
        step_sequence=1,
        object_name="Account",
        operation="insert",
        output_storage=storage,
    )

    assert failed == 1
    assert processed == 3  # 2 success + 1 error


# ── BulkAPIError handling (unchanged from original behaviour) ─────────────────


@pytest.mark.asyncio
async def test_bulk_api_error_on_success_download_is_caught(tmp_path):
    """BulkAPIError on get_success_results is caught and logged; no re-raise."""
    storage = LocalOutputStorage(str(tmp_path))
    jr = _make_job_record()
    client = _make_bulk_client(success_exc=BulkAPIError("SF error"))

    # Must not raise
    processed, failed = await download_and_persist_results(
        bulk_client=client,
        sf_job_id="JOB001",
        job_record=jr,
        run_id="run-001",
        plan_id="plan-001-fixture",
        plan_name="Test Plan",
        step_sequence=1,
        object_name="Account",
        operation="insert",
        output_storage=storage,
    )

    assert jr.success_file_path is None
    assert processed == 0
