"""Tests for orchestrator query-step dispatch (SFBL-170).

Covers:
- Query step happy path: JobRecord populated with query-semantic fields; run
  transitions through expected states; WS events emitted.
- Query step failure (BulkQueryJobFailed): JobRecord marked failed;
  abort_on_step_failure=True aborts the run; abort_on_step_failure=False
  continues.
- Query step writes to correct OutputStorage instance when plan has
  output_connection_id set vs unset (mock the factory).
- Mixed plan: DML step → query step → DML step all run in order, each
  populating the correct JobRecord shape.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection import Connection
from app.models.job import JobRecord, JobStatus
from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep, Operation
from app.services.bulk_query_executor import BulkQueryJobFailed, BulkQueryResult
from app.services.orchestrator import _execute_run

from tests.conftest import _TestSession as _SessionFactory


# ── Shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _clean_tables():
    """Truncate all rows between tests for isolation."""
    yield
    from sqlalchemy import delete
    from app.models.input_connection import InputConnection

    async with _SessionFactory() as s:
        for model in [JobRecord, LoadRun, LoadStep, LoadPlan, Connection, InputConnection]:
            await s.execute(delete(model))
        await s.commit()


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with _SessionFactory() as session:
        yield session


def make_db_factory(session: AsyncSession):
    """Return a db_factory that yields the given session."""

    @asynccontextmanager
    async def _factory() -> AsyncGenerator[AsyncSession, None]:
        yield session

    return _factory


# ── Test data helpers ─────────────────────────────────────────────────────────


async def _make_connection(db: AsyncSession) -> Connection:
    conn = Connection(
        id=str(uuid.uuid4()),
        name="Test Org",
        instance_url="https://test.salesforce.com",
        login_url="https://test.salesforce.com",
        client_id="client_id",
        private_key="encrypted_key",
        username="user@example.com",
        is_sandbox=True,
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return conn


async def _make_plan(
    db: AsyncSession,
    connection: Connection,
    *,
    abort_on_step_failure: bool = True,
    error_threshold_pct: float = 10.0,
    max_parallel_jobs: int = 3,
    output_connection_id: str | None = None,
) -> LoadPlan:
    plan = LoadPlan(
        id=str(uuid.uuid4()),
        connection_id=connection.id,
        name="Test Plan",
        abort_on_step_failure=abort_on_step_failure,
        error_threshold_pct=error_threshold_pct,
        max_parallel_jobs=max_parallel_jobs,
        output_connection_id=output_connection_id,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan


async def _make_query_step(
    db: AsyncSession,
    plan: LoadPlan,
    *,
    sequence: int = 1,
    object_name: str = "Account",
    operation: Operation = Operation.query,
    soql: str = "SELECT Id, Name FROM Account",
) -> LoadStep:
    step = LoadStep(
        id=str(uuid.uuid4()),
        load_plan_id=plan.id,
        sequence=sequence,
        object_name=object_name,
        operation=operation,
        soql=soql,
        csv_file_pattern=None,  # not required for query ops
    )
    db.add(step)
    await db.commit()
    await db.refresh(step)
    return step


async def _make_dml_step(
    db: AsyncSession,
    plan: LoadPlan,
    *,
    sequence: int = 1,
    object_name: str = "Contact",
    operation: Operation = Operation.insert,
) -> LoadStep:
    step = LoadStep(
        id=str(uuid.uuid4()),
        load_plan_id=plan.id,
        sequence=sequence,
        object_name=object_name,
        operation=operation,
        csv_file_pattern="contacts_*.csv",
        partition_size=10_000,
    )
    db.add(step)
    await db.commit()
    await db.refresh(step)
    return step


async def _make_run(db: AsyncSession, plan: LoadPlan) -> LoadRun:
    run = LoadRun(
        id=str(uuid.uuid4()),
        load_plan_id=plan.id,
        status=RunStatus.pending,
        initiated_by="test",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


# ── Mock helpers ──────────────────────────────────────────────────────────────

CSV_HEADER = b"Name,ExternalId__c\n"
CSV_2_ROWS = CSV_HEADER + b"Acme,EXT-001\nBeta,EXT-002\n"


def _make_bulk_client_mock(
    *,
    sf_job_id: str = "JOB001",
    terminal_state: str = "JobComplete",
    success_csv: bytes = CSV_2_ROWS,
    error_csv: bytes = CSV_HEADER,
    unprocessed_csv: bytes = CSV_HEADER,
) -> MagicMock:
    """Build a MagicMock that behaves like SalesforceBulkClient (DML path)."""
    mock = MagicMock()
    mock.create_job = AsyncMock(return_value=sf_job_id)
    mock.upload_csv = AsyncMock(return_value=None)
    mock.close_job = AsyncMock(return_value=None)
    mock.poll_job_once = AsyncMock(return_value=(terminal_state, 0, 0, {"state": terminal_state}))
    mock.get_success_results = AsyncMock(return_value=success_csv)
    mock.get_failed_results = AsyncMock(return_value=error_csv)
    mock.get_unprocessed_results = AsyncMock(return_value=unprocessed_csv)
    mock.abort_job = AsyncMock(return_value=None)
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


def _make_storage_mock(rel_paths: list[str] | None = None, provider: str = "local") -> MagicMock:
    """Return a mock InputStorage."""
    mock = MagicMock()
    mock.provider = provider
    mock.discover_files.return_value = rel_paths if rel_paths is not None else ["contacts.csv"]
    return mock


def _make_output_storage_mock() -> MagicMock:
    """Return a mock OutputStorage."""
    mock = MagicMock()
    mock.write_bytes = MagicMock(side_effect=lambda path, data: path)
    mock.read_bytes = MagicMock(return_value=b"")
    return mock


def _make_query_result(
    *,
    row_count: int = 100,
    artefact_uri: str = "run-abc/01-Account-20260101T000000.csv",
    sf_job_response: dict | None = None,
) -> BulkQueryResult:
    return BulkQueryResult(
        row_count=row_count,
        byte_count=row_count * 20,
        artefact_uri=artefact_uri,
        final_state="JobComplete",
        sf_job_response=sf_job_response,
    )


# ── Tests: Query step happy path ──────────────────────────────────────────────


async def test_query_step_happy_path(db: AsyncSession, tmp_path):
    """Query step: JobRecord populated with query-semantic fields; run completed."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    step = await _make_query_step(db, plan, soql="SELECT Id FROM Account")
    run = await _make_run(db, plan)

    query_result = _make_query_result(
        row_count=50,
        artefact_uri="run-abc/01-Account-20260101T000000.csv",
        sf_job_response={
            "id": "750xx0000000001",
            "state": "JobComplete",
            "operation": "query",
            "object": "Account",
            "numberRecordsProcessed": 50,
        },
    )
    fake_run_bulk_query = AsyncMock(return_value=query_result)
    bulk_mock = _make_bulk_client_mock()
    db_factory = make_db_factory(db)

    broadcast_events: list[dict] = []

    async def capture_broadcast(run_id, event):
        broadcast_events.append(event)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.get_storage", new=AsyncMock(return_value=_make_storage_mock())),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", side_effect=capture_broadcast),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
        patch("app.services.orchestrator.run_bulk_query", new=fake_run_bulk_query),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.completed
    assert run.total_records == 50
    assert run.total_success == 50
    assert run.total_errors == 0

    result = await db.execute(select(JobRecord).where(JobRecord.load_run_id == run.id))
    jobs = list(result.scalars().all())
    assert len(jobs) == 1

    job = jobs[0]
    assert job.load_step_id == step.id
    assert job.partition_index == 0
    assert job.status == JobStatus.job_complete
    assert job.success_file_path == "run-abc/01-Account-20260101T000000.csv"
    assert job.records_processed == 50
    assert job.total_records == 50
    assert job.records_failed == 0
    assert job.error_file_path is None
    assert job.unprocessed_file_path is None

    # Raw Salesforce payload captured on the JobRecord (parity with DML)
    import json as _json
    assert job.sf_api_response is not None
    parsed = _json.loads(job.sf_api_response)
    assert parsed["id"] == "750xx0000000001"
    assert parsed["state"] == "JobComplete"

    # Verify run_bulk_query was called with the correct args
    fake_run_bulk_query.assert_called_once()
    call_kwargs = fake_run_bulk_query.call_args.kwargs
    assert call_kwargs["soql"] == "SELECT Id FROM Account"
    assert call_kwargs["operation"] == "query"
    assert call_kwargs["instance_url"] == "https://test.salesforce.com"
    assert call_kwargs["access_token"] == "token"

    # SFBL-164 layout: {plan_short}-{plan_slug}/{run_short}/
    #   {sequence:02d}_{object_slug}_{operation}_{step_short}/partition_0_results.csv
    rel = call_kwargs["relative_path"]
    assert rel.startswith(f"{plan.id[:8]}-")
    assert f"/{run.id[:8]}/" in rel
    assert f"/01_account_query_{step.id[:8]}/" in rel
    assert rel.endswith("/partition_0_results.csv")

    # WS events should include at least step-started and job-status events
    event_names = [e.get("event_name") for e in broadcast_events]
    assert "run.started" in event_names
    assert "run.completed" in event_names


async def test_query_step_failure_aborts_run(db: AsyncSession, tmp_path):
    """Query step raises BulkQueryJobFailed + abort_on_step_failure=True → aborted."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn, abort_on_step_failure=True, error_threshold_pct=0.0)
    step = await _make_query_step(db, plan)
    run = await _make_run(db, plan)

    fake_run_bulk_query = AsyncMock(
        side_effect=BulkQueryJobFailed(
            "Bulk query job JOB-Q001 ended in state 'Failed'",
            final_state="Failed",
        )
    )
    bulk_mock = _make_bulk_client_mock()
    db_factory = make_db_factory(db)

    broadcast_events: list[dict] = []

    async def capture_broadcast(run_id, event):
        broadcast_events.append(event)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.get_storage", new=AsyncMock(return_value=_make_storage_mock())),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", side_effect=capture_broadcast),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
        patch("app.services.orchestrator.run_bulk_query", new=fake_run_bulk_query),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.aborted

    # JobRecord should be marked failed
    result = await db.execute(select(JobRecord).where(JobRecord.load_run_id == run.id))
    jobs = list(result.scalars().all())
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.failed

    # WS events should include run.aborted
    event_names = [e.get("event_name") for e in broadcast_events]
    assert "run.aborted" in event_names


async def test_query_step_failure_no_abort(db: AsyncSession, tmp_path):
    """Query step fails + abort_on_step_failure=False → completed_with_errors."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn, abort_on_step_failure=False, error_threshold_pct=0.0)
    step = await _make_query_step(db, plan)
    run = await _make_run(db, plan)

    fake_run_bulk_query = AsyncMock(
        side_effect=BulkQueryJobFailed(
            "Bulk query job JOB-Q001 ended in state 'Failed'",
            final_state="Failed",
        )
    )
    bulk_mock = _make_bulk_client_mock()
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.get_storage", new=AsyncMock(return_value=_make_storage_mock())),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
        patch("app.services.orchestrator.run_bulk_query", new=fake_run_bulk_query),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.completed_with_errors

    result = await db.execute(select(JobRecord).where(JobRecord.load_run_id == run.id))
    jobs = list(result.scalars().all())
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.failed


async def test_query_step_uses_local_output_storage_when_no_connection(db: AsyncSession, tmp_path):
    """Without output_connection_id, the query artefact goes to LocalOutputStorage."""
    from app.services.output_storage import LocalOutputStorage

    conn = await _make_connection(db)
    plan = await _make_plan(db, conn, output_connection_id=None)
    step = await _make_query_step(db, plan)
    run = await _make_run(db, plan)

    captured_storage = []

    async def fake_run_bulk_query(
        *, soql, operation, instance_url, access_token, output_storage, relative_path, **kwargs
    ):
        captured_storage.append(output_storage)
        return _make_query_result(row_count=10)

    bulk_mock = _make_bulk_client_mock()
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.get_storage", new=AsyncMock(return_value=_make_storage_mock())),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
        patch("app.services.orchestrator.run_bulk_query", new=fake_run_bulk_query),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.completed
    assert len(captured_storage) == 1
    assert isinstance(captured_storage[0], LocalOutputStorage)


async def test_query_step_uses_s3_output_storage_when_connection_set(db: AsyncSession, tmp_path):
    """With output_connection_id set, the factory is called and S3 storage is used."""
    from app.models.input_connection import InputConnection
    from app.services.output_storage import S3OutputStorage

    conn = await _make_connection(db)

    # Create a real InputConnection to satisfy the FK constraint
    output_conn = InputConnection(
        id=str(uuid.uuid4()),
        name="S3 Output Bucket",
        provider="s3",
        direction="out",
        bucket="output-bucket",
        access_key_id="fake-key",
        secret_access_key="fake-secret",
    )
    db.add(output_conn)
    await db.commit()
    await db.refresh(output_conn)

    plan = await _make_plan(db, conn, output_connection_id=output_conn.id)
    step = await _make_query_step(db, plan)
    run = await _make_run(db, plan)

    captured_storage = []

    async def fake_run_bulk_query(
        *, soql, operation, instance_url, access_token, output_storage, relative_path, **kwargs
    ):
        captured_storage.append(output_storage)
        return _make_query_result(row_count=5)

    mock_s3_storage = MagicMock(spec=S3OutputStorage)
    mock_get_output_storage = AsyncMock(return_value=mock_s3_storage)

    bulk_mock = _make_bulk_client_mock()
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.get_storage", new=AsyncMock(return_value=_make_storage_mock())),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
        patch("app.services.orchestrator.run_bulk_query", new=fake_run_bulk_query),
        patch("app.services.run_coordinator.get_output_storage", new=mock_get_output_storage),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.completed
    # The mocked storage instance should have been passed to run_bulk_query
    assert len(captured_storage) == 1
    assert captured_storage[0] is mock_s3_storage
    # Factory was called with the plan's output_connection_id
    mock_get_output_storage.assert_called_once_with(output_conn.id, db)


async def test_mixed_plan_dml_query_dml(db: AsyncSession, tmp_path):
    """Mixed plan: DML → query → DML runs in sequence, correct JobRecord shapes."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)

    # Steps ordered by sequence: DML(seq=1), query(seq=2), DML(seq=3)
    dml_step1 = await _make_dml_step(db, plan, sequence=1, object_name="Contact")
    query_step = await _make_query_step(db, plan, sequence=2, object_name="Account")
    dml_step2 = await _make_dml_step(db, plan, sequence=3, object_name="Lead")
    run = await _make_run(db, plan)

    query_result = _make_query_result(row_count=75)
    fake_run_bulk_query = AsyncMock(return_value=query_result)

    # DML jobs return 2 rows success each
    bulk_mock = _make_bulk_client_mock(
        success_csv=CSV_2_ROWS,
        error_csv=CSV_HEADER,
    )
    db_factory = make_db_factory(db)

    execution_order: list[str] = []

    async def tracking_run_bulk_query(**kwargs):
        execution_order.append(f"query:{kwargs['soql']}")
        return _make_query_result(row_count=75)

    original_poll_once = bulk_mock.poll_job_once

    async def tracking_poll_once(sf_job_id: str):
        execution_order.append(f"dml:{sf_job_id}")
        return ("JobComplete", 0, 0, {"state": "JobComplete"})

    bulk_mock.poll_job_once = tracking_poll_once
    # Two DML jobs get different IDs
    bulk_mock.create_job = AsyncMock(side_effect=["DML_JOB1", "DML_JOB2"])

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.get_storage", new=AsyncMock(return_value=_make_storage_mock(["file.csv"]))),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
        patch("app.services.orchestrator.run_bulk_query", new=tracking_run_bulk_query),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.completed

    # 2 DML rows + 75 query rows + 2 DML rows = 79 total
    assert run.total_records == 79
    assert run.total_success == 79
    assert run.total_errors == 0

    # Verify execution order: DML1 → query → DML2
    assert execution_order[0] == "dml:DML_JOB1"
    assert execution_order[1].startswith("query:")
    assert execution_order[2] == "dml:DML_JOB2"

    # Verify all three JobRecords exist
    result = await db.execute(select(JobRecord).where(JobRecord.load_run_id == run.id))
    jobs = list(result.scalars().all())
    assert len(jobs) == 3

    # Classify by step
    jobs_by_step = {j.load_step_id: j for j in jobs}

    # DML jobs
    dml1_job = jobs_by_step[dml_step1.id]
    assert dml1_job.status == JobStatus.job_complete
    assert dml1_job.sf_job_id == "DML_JOB1"

    # Query job — has query-semantic fields
    query_job = jobs_by_step[query_step.id]
    assert query_job.status == JobStatus.job_complete
    assert query_job.partition_index == 0
    assert query_job.records_processed == 75
    assert query_job.total_records == 75
    assert query_job.records_failed == 0
    assert query_job.error_file_path is None
    assert query_job.unprocessed_file_path is None
    # No DML sf_job_id on query job
    assert query_job.sf_job_id is None

    dml2_job = jobs_by_step[dml_step2.id]
    assert dml2_job.status == JobStatus.job_complete
    assert dml2_job.sf_job_id == "DML_JOB2"


async def test_query_step_unexpected_exception_marks_failed(db: AsyncSession, tmp_path):
    """A non-BulkQueryJobFailed exception from run_bulk_query marks job failed."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn, abort_on_step_failure=False, error_threshold_pct=0.0)
    step = await _make_query_step(db, plan)
    run = await _make_run(db, plan)

    fake_run_bulk_query = AsyncMock(
        side_effect=RuntimeError("Unexpected network error")
    )
    bulk_mock = _make_bulk_client_mock()
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.get_storage", new=AsyncMock(return_value=_make_storage_mock())),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
        patch("app.services.orchestrator.run_bulk_query", new=fake_run_bulk_query),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.completed_with_errors

    result = await db.execute(select(JobRecord).where(JobRecord.load_run_id == run.id))
    jobs = list(result.scalars().all())
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.failed


async def test_query_step_queryAll_operation(db: AsyncSession, tmp_path):
    """queryAll operation is routed to the query path (not DML)."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    step = await _make_query_step(
        db, plan,
        operation=Operation.queryAll,
        soql="SELECT Id FROM Contact",
    )
    run = await _make_run(db, plan)

    fake_run_bulk_query = AsyncMock(return_value=_make_query_result(row_count=30))
    bulk_mock = _make_bulk_client_mock()
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.get_storage", new=AsyncMock(return_value=_make_storage_mock())),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
        patch("app.services.orchestrator.run_bulk_query", new=fake_run_bulk_query),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.completed
    assert run.total_records == 30

    # Verify queryAll was passed as the operation
    call_kwargs = fake_run_bulk_query.call_args.kwargs
    assert call_kwargs["operation"] == "queryAll"
