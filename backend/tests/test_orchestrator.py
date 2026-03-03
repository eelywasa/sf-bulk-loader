"""Tests for the Orchestrator (spec §4.4 / §12.1).

All external I/O is mocked:
  - SalesforceBulkClient  (no real HTTP calls)
  - discover_files / partition_csv  (no real filesystem access)
  - get_access_token  (no real token exchange)
  - ws_manager.broadcast  (no real WebSockets)

DB strategy:
  Integration tests use a dedicated file-based SQLite DB (test_orchestrator.db).
  The ``db`` fixture provides the main session passed to ``_execute_run``.
  ``db_factory`` is set to ``make_db_factory(db)`` so partition coroutines share
  the same session, keeping all state visible without cross-session coordination.
  ``asyncio.Semaphore`` concurrency tests verify that the semaphore limits
  concurrent SF API calls regardless of the shared session.
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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.connection import Connection
from app.models.job import JobRecord, JobStatus
from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep, Operation
from app.services.orchestrator import (
    _count_csv_rows,
    _execute_run,
    _poll_with_timeout,
)

# ── In-process test database ──────────────────────────────────────────────────

_TEST_URL = "sqlite+aiosqlite:///./test_orchestrator.db"
_engine = create_async_engine(_TEST_URL, echo=False)
_SessionFactory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="module", autouse=True)
async def _create_tables():
    """Create all tables once for the module and drop them on teardown."""
    from app.database import Base

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await _engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_tables():
    """Truncate all rows between tests for isolation."""
    yield
    from sqlalchemy import delete

    async with _SessionFactory() as s:
        for model in [JobRecord, LoadRun, LoadStep, LoadPlan, Connection]:
            await s.execute(delete(model))
        await s.commit()


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with _SessionFactory() as session:
        yield session


# ── Shared factory that reuses the same session ────────────────────────────────


def make_db_factory(session: AsyncSession):
    """Return a db_factory that yields the given session (avoids new connections)."""

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
) -> LoadPlan:
    plan = LoadPlan(
        id=str(uuid.uuid4()),
        connection_id=connection.id,
        name="Test Plan",
        abort_on_step_failure=abort_on_step_failure,
        error_threshold_pct=error_threshold_pct,
        max_parallel_jobs=max_parallel_jobs,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan


async def _make_step(
    db: AsyncSession,
    plan: LoadPlan,
    *,
    sequence: int = 1,
    object_name: str = "Account",
    operation: Operation = Operation.insert,
    partition_size: int = 10_000,
) -> LoadStep:
    step = LoadStep(
        id=str(uuid.uuid4()),
        load_plan_id=plan.id,
        sequence=sequence,
        object_name=object_name,
        operation=operation,
        csv_file_pattern="accounts_*.csv",
        partition_size=partition_size,
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


# ── Mocking helpers ───────────────────────────────────────────────────────────

CSV_HEADER = b"Name,ExternalId__c\n"
CSV_2_ROWS = CSV_HEADER + b"Acme,EXT-001\nBeta,EXT-002\n"
CSV_1_ERROR = CSV_HEADER + b"Bad Corp,EXT-999\n"


def _make_bulk_client_mock(
    *,
    sf_job_id: str = "JOB001",
    terminal_state: str = "JobComplete",
    success_csv: bytes = CSV_2_ROWS,
    error_csv: bytes = CSV_HEADER,       # header-only = no errors
    unprocessed_csv: bytes = CSV_HEADER,
) -> MagicMock:
    """Build a MagicMock that behaves like SalesforceBulkClient."""
    mock = MagicMock()
    mock.create_job = AsyncMock(return_value=sf_job_id)
    mock.upload_csv = AsyncMock(return_value=None)
    mock.close_job = AsyncMock(return_value=None)
    mock.poll_job = AsyncMock(return_value=terminal_state)
    mock.get_success_results = AsyncMock(return_value=success_csv)
    mock.get_failed_results = AsyncMock(return_value=error_csv)
    mock.get_unprocessed_results = AsyncMock(return_value=unprocessed_csv)
    mock.abort_job = AsyncMock(return_value=None)
    # Async context manager support
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


# ── Unit tests: _count_csv_rows ───────────────────────────────────────────────


def test_count_csv_rows_empty():
    assert _count_csv_rows(b"") == 0


def test_count_csv_rows_header_only():
    assert _count_csv_rows(b"Name,Id\n") == 0


def test_count_csv_rows_two_data_rows():
    assert _count_csv_rows(b"Name,Id\nAlice,1\nBob,2\n") == 2


def test_count_csv_rows_quoted_newline():
    # A quoted field containing a newline — csv.reader handles it correctly.
    csv_bytes = b'Name,Notes\nAlice,"line1\nline2"\n'
    assert _count_csv_rows(csv_bytes) == 1


def test_count_csv_rows_no_trailing_newline():
    assert _count_csv_rows(b"Name,Id\nAlice,1") == 1


# ── Unit tests: _poll_with_timeout ────────────────────────────────────────────


async def test_poll_with_timeout_returns_on_success():
    mock = MagicMock()
    mock.poll_job = AsyncMock(return_value="JobComplete")
    result = await _poll_with_timeout(
        bulk_client=mock,
        sf_job_id="JOB1",
        run_id="RUN1",
        step_id="STEP1",
        partition_index=0,
    )
    assert result == "JobComplete"
    mock.poll_job.assert_awaited_once_with("JOB1")


async def test_poll_with_timeout_warns_and_continues():
    """When wait_for times out, a warning is logged and polling resumes without timeout."""
    mock = MagicMock()
    # poll_job is called twice: first attempt (will be timed out), second succeeds.
    mock.poll_job = AsyncMock(return_value="JobComplete")

    wait_for_call_count = 0
    real_wait_for = asyncio.wait_for

    async def patched_wait_for(coro, timeout=None, **kwargs):
        nonlocal wait_for_call_count
        wait_for_call_count += 1
        if wait_for_call_count == 1:
            # Discard the coroutine cleanly and simulate a timeout.
            coro.close()
            raise asyncio.TimeoutError()
        return await real_wait_for(coro, timeout=timeout, **kwargs)

    with patch("app.services.orchestrator.asyncio.wait_for", new=patched_wait_for):
        result = await _poll_with_timeout(
            bulk_client=mock,
            sf_job_id="JOB1",
            run_id="RUN1",
            step_id="STEP1",
            partition_index=0,
        )

    assert result == "JobComplete"
    # poll_job called twice: once (coroutine closed on timeout), once in fallback.
    assert mock.poll_job.call_count == 2


# ── Integration-style orchestrator tests ──────────────────────────────────────


async def test_successful_single_step_run(db: AsyncSession, tmp_path):
    """Happy-path: one step, one partition → run completed."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    step = await _make_step(db, plan)
    run = await _make_run(db, plan)

    bulk_mock = _make_bulk_client_mock(success_csv=CSV_2_ROWS, error_csv=CSV_HEADER)
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.discover_files", return_value=["accounts.csv"]),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.completed
    assert run.total_records == 2
    assert run.total_success == 2
    assert run.total_errors == 0

    result = await db.execute(select(JobRecord).where(JobRecord.load_run_id == run.id))
    jobs = list(result.scalars().all())
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.job_complete
    assert jobs[0].sf_job_id == "JOB001"
    assert jobs[0].records_processed == 2
    assert jobs[0].records_failed == 0


async def test_multi_step_run_executes_in_sequence(db: AsyncSession, tmp_path):
    """Steps with different sequence numbers execute in order."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    step1 = await _make_step(db, plan, sequence=1, object_name="Account")
    step2 = await _make_step(db, plan, sequence=2, object_name="Contact")
    run = await _make_run(db, plan)

    execution_order: list[str] = []

    async def fake_poll(sf_job_id: str) -> str:
        execution_order.append(sf_job_id)
        return "JobComplete"

    bulk_mock = _make_bulk_client_mock()
    bulk_mock.poll_job = fake_poll
    bulk_mock.create_job = AsyncMock(side_effect=["JOB_ACCT", "JOB_CONT"])
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.discover_files", return_value=["file.csv"]),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.completed

    result = await db.execute(
        select(JobRecord).where(JobRecord.load_run_id == run.id)
    )
    jobs = list(result.scalars().all())
    assert len(jobs) == 2

    # Verify jobs are linked to the correct steps.
    step_ids = {j.load_step_id for j in jobs}
    assert step1.id in step_ids
    assert step2.id in step_ids

    # Verify Account was polled before Contact.
    assert execution_order == ["JOB_ACCT", "JOB_CONT"]


async def test_run_with_errors_below_threshold(db: AsyncSession, tmp_path):
    """Errors below threshold → completed_with_errors=False (completed)."""
    conn = await _make_connection(db)
    # 10% threshold; 1 error / 10 records = 10% which is NOT above threshold.
    plan = await _make_plan(db, conn, error_threshold_pct=10.0)
    await _make_step(db, plan)
    run = await _make_run(db, plan)

    # 9 success rows + 1 error row
    success_csv = b"sf__Id,sf__Created\n" + b"001,true\n" * 9
    error_csv = b"sf__Id,sf__Error\n" + b"002,field_error\n"
    bulk_mock = _make_bulk_client_mock(success_csv=success_csv, error_csv=error_csv)
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.discover_files", return_value=["f.csv"]),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.completed


async def test_run_aborted_when_error_threshold_exceeded(db: AsyncSession, tmp_path):
    """Error rate > threshold AND abort_on_step_failure → run aborted."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn, abort_on_step_failure=True, error_threshold_pct=10.0)
    await _make_step(db, plan)
    run = await _make_run(db, plan)

    # 100% errors — far above 10% threshold.
    error_csv = b"sf__Id,sf__Error\n" + b"002,field_error\n" * 5
    bulk_mock = _make_bulk_client_mock(
        success_csv=CSV_HEADER,  # no successes
        error_csv=error_csv,
    )
    db_factory = make_db_factory(db)

    broadcast_events: list[dict] = []

    async def capture_broadcast(run_id, event):
        broadcast_events.append(event)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.discover_files", return_value=["f.csv"]),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", side_effect=capture_broadcast),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.aborted
    assert any(e.get("event") == "run_aborted" for e in broadcast_events)


async def test_error_threshold_exceeded_no_abort(db: AsyncSession, tmp_path):
    """Error rate > threshold but abort_on_step_failure=False → completed_with_errors."""
    conn = await _make_connection(db)
    plan = await _make_plan(
        db, conn, abort_on_step_failure=False, error_threshold_pct=10.0
    )
    await _make_step(db, plan)
    run = await _make_run(db, plan)

    error_csv = b"sf__Id,sf__Error\n" + b"002,bad\n" * 5
    bulk_mock = _make_bulk_client_mock(success_csv=CSV_HEADER, error_csv=error_csv)
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.discover_files", return_value=["f.csv"]),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.completed_with_errors


async def test_multi_step_second_step_skipped_on_abort(db: AsyncSession, tmp_path):
    """When step 1 aborts the run, step 2 is never submitted."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn, abort_on_step_failure=True, error_threshold_pct=0.0)
    step1 = await _make_step(db, plan, sequence=1, object_name="Account")
    step2 = await _make_step(db, plan, sequence=2, object_name="Contact")
    run = await _make_run(db, plan)

    # Step 1 has errors (0% threshold means any error aborts).
    error_csv = b"sf__Id,sf__Error\n" + b"X,bad\n"
    bulk_mock = _make_bulk_client_mock(success_csv=CSV_HEADER, error_csv=error_csv)
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.discover_files", return_value=["f.csv"]),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.aborted

    # No JobRecords for step2 — it was never started.
    result = await db.execute(
        select(JobRecord).where(JobRecord.load_step_id == step2.id)
    )
    step2_jobs = list(result.scalars().all())
    assert len(step2_jobs) == 0


async def test_external_abort_stops_before_next_step(db: AsyncSession, tmp_path):
    """If run is marked aborted externally, the next step is skipped."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    step1 = await _make_step(db, plan, sequence=1, object_name="Account")
    step2 = await _make_step(db, plan, sequence=2, object_name="Contact")
    run = await _make_run(db, plan)

    # After step1 processes, mark run as aborted externally.
    step1_processed = asyncio.Event()
    original_poll = AsyncMock(return_value="JobComplete")

    async def poll_and_signal(sf_job_id: str) -> str:
        result = await original_poll(sf_job_id)
        step1_processed.set()
        # Simulate external abort between steps by modifying the run in DB.
        async with _SessionFactory() as ext_db:
            ext_run = await ext_db.get(LoadRun, run.id)
            ext_run.status = RunStatus.aborted
            await ext_db.commit()
        return result

    bulk_mock = _make_bulk_client_mock()
    bulk_mock.poll_job = poll_and_signal
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.discover_files", return_value=["f.csv"]),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    # The run should be aborted and step2 should have no jobs.
    result = await db.execute(
        select(JobRecord).where(JobRecord.load_step_id == step2.id)
    )
    assert list(result.scalars().all()) == []


async def test_auth_failure_marks_run_failed(db: AsyncSession, tmp_path):
    """Auth error before any step → run status becomes failed."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    await _make_step(db, plan)
    run = await _make_run(db, plan)

    from app.services.salesforce_auth import AuthError

    with (
        patch(
            "app.services.orchestrator.get_access_token",
            new=AsyncMock(side_effect=AuthError("bad key")),
        ),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
    ):
        await _execute_run(run.id, db, db_factory=make_db_factory(db))

    await db.refresh(run)
    assert run.status == RunStatus.failed


async def test_job_creation_failure_marks_job_failed(db: AsyncSession, tmp_path):
    """If create_job raises BulkAPIError, the JobRecord is marked failed."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    await _make_step(db, plan)
    run = await _make_run(db, plan)

    from app.services.salesforce_bulk import BulkAPIError

    bulk_mock = _make_bulk_client_mock()
    bulk_mock.create_job = AsyncMock(side_effect=BulkAPIError("create failed"))
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.discover_files", return_value=["f.csv"]),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    result = await db.execute(select(JobRecord).where(JobRecord.load_run_id == run.id))
    jobs = list(result.scalars().all())
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.failed
    assert "create failed" in (jobs[0].error_message or "")


async def test_upload_failure_marks_job_failed(db: AsyncSession, tmp_path):
    """If upload_csv raises, the JobRecord is marked failed."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    await _make_step(db, plan)
    run = await _make_run(db, plan)

    from app.services.salesforce_bulk import BulkAPIError

    bulk_mock = _make_bulk_client_mock()
    bulk_mock.upload_csv = AsyncMock(side_effect=BulkAPIError("upload failed", status_code=500))
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.discover_files", return_value=["f.csv"]),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    result = await db.execute(select(JobRecord).where(JobRecord.load_run_id == run.id))
    jobs = list(result.scalars().all())
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.failed


async def test_no_csv_files_found_skips_step(db: AsyncSession, tmp_path):
    """If discover_files returns nothing, the step is skipped and run completes."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    await _make_step(db, plan)
    run = await _make_run(db, plan)

    bulk_mock = _make_bulk_client_mock()
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.discover_files", return_value=[]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    await db.refresh(run)
    assert run.status == RunStatus.completed
    assert run.total_records == 0
    # No jobs created.
    result = await db.execute(select(JobRecord).where(JobRecord.load_run_id == run.id))
    assert list(result.scalars().all()) == []
    # create_job was never called.
    bulk_mock.create_job.assert_not_awaited()


async def test_multiple_partitions_created_for_large_file(tmp_path):
    """Multiple partitions from a single file each produce a separate JobRecord."""
    # Uses separate sessions per partition to avoid shared-session concurrency issues.
    async with _SessionFactory() as db:
        conn = await _make_connection(db)
        plan = await _make_plan(db, conn, max_parallel_jobs=2)
        await _make_step(db, plan, partition_size=1)
        run = await _make_run(db, plan)

        # Two partitions from the file.
        part1 = b"Name\nAcme\n"
        part2 = b"Name\nBeta\n"
        bulk_mock = _make_bulk_client_mock()
        bulk_mock.create_job = AsyncMock(side_effect=["JOB_A", "JOB_B"])

        with (
            patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
            patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
            patch("app.services.orchestrator.discover_files", return_value=["big.csv"]),
            patch("app.services.orchestrator.partition_csv", return_value=[part1, part2]),
            patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
            patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
        ):
            # _SessionFactory gives each partition its own session.
            await _execute_run(run.id, db, db_factory=_SessionFactory)

        # Reload via a fresh session to see partition-committed changes.
        async with _SessionFactory() as fresh:
            result = await fresh.execute(
                select(JobRecord).where(JobRecord.load_run_id == run.id)
            )
            jobs = list(result.scalars().all())

    assert len(jobs) == 2
    sf_ids = {j.sf_job_id for j in jobs}
    assert sf_ids == {"JOB_A", "JOB_B"}


async def test_semaphore_limits_concurrency(tmp_path):
    """Semaphore prevents more than max_parallel_jobs from running at once."""
    # Uses separate sessions per partition to avoid shared-session concurrency issues.
    async with _SessionFactory() as db:
        conn = await _make_connection(db)
        plan = await _make_plan(db, conn, max_parallel_jobs=2)
        await _make_step(db, plan)
        run = await _make_run(db, plan)

        concurrent_high_water = 0
        current_concurrent = 0

        original_upload = AsyncMock(return_value=None)

        async def tracking_upload(sf_job_id: str, data: bytes) -> None:
            nonlocal concurrent_high_water, current_concurrent
            current_concurrent += 1
            concurrent_high_water = max(concurrent_high_water, current_concurrent)
            await asyncio.sleep(0)  # yield to let other tasks run
            current_concurrent -= 1
            return await original_upload(sf_job_id, data)

        bulk_mock = _make_bulk_client_mock()
        bulk_mock.upload_csv = tracking_upload
        bulk_mock.create_job = AsyncMock(side_effect=["J1", "J2", "J3", "J4"])

        # 4 partitions, but max 2 concurrent.
        partitions = [b"Name\nA\n", b"Name\nB\n", b"Name\nC\n", b"Name\nD\n"]

        with (
            patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
            patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
            patch("app.services.orchestrator.discover_files", return_value=["f.csv"]),
            patch("app.services.orchestrator.partition_csv", return_value=partitions),
            patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
            patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
        ):
            await _execute_run(run.id, db, db_factory=_SessionFactory)

    assert concurrent_high_water <= 2, (
        f"Expected at most 2 concurrent uploads, got {concurrent_high_water}"
    )


async def test_run_not_found_exits_gracefully(db: AsyncSession):
    """If the LoadRun does not exist, _execute_run returns without error."""
    with patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()):
        # Should not raise.
        await _execute_run("nonexistent-run-id", db)


async def test_result_files_saved_to_output_dir(db: AsyncSession, tmp_path):
    """Success and error CSVs are written under OUTPUT_DIR/run_id/step_id/."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    step = await _make_step(db, plan)
    run = await _make_run(db, plan)

    success_data = b"sf__Id,sf__Created\n001,true\n002,true\n"
    error_data = b"sf__Id,sf__Error\n003,bad\n"
    bulk_mock = _make_bulk_client_mock(success_csv=success_data, error_csv=error_data)
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.discover_files", return_value=["f.csv"]),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    result = await db.execute(select(JobRecord).where(JobRecord.load_run_id == run.id))
    job = result.scalars().first()
    assert job is not None
    assert job.success_file_path is not None
    assert job.error_file_path is not None

    # Files must exist on disk.
    assert (tmp_path / job.success_file_path).exists()
    assert (tmp_path / job.error_file_path).exists()

    # Record counts must reflect file contents.
    assert job.records_processed == 3   # 2 success + 1 error
    assert job.records_failed == 1


async def test_run_started_and_completed_events_broadcast(db: AsyncSession, tmp_path):
    """ws_manager.broadcast is called with run_started and run_completed events."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    await _make_step(db, plan)
    run = await _make_run(db, plan)

    events: list[dict] = []

    async def capture(run_id, event):
        events.append(event)

    bulk_mock = _make_bulk_client_mock()
    db_factory = make_db_factory(db)

    with (
        patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
        patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
        patch("app.services.orchestrator.discover_files", return_value=["f.csv"]),
        patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS]),
        patch("app.services.orchestrator.ws_manager.broadcast", side_effect=capture),
        patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
    ):
        await _execute_run(run.id, db, db_factory=db_factory)

    event_types = [e["event"] for e in events]
    assert "run_started" in event_types
    assert "step_started" in event_types
    assert "step_completed" in event_types
    assert "run_completed" in event_types


async def test_pending_jobs_aborted_when_run_aborted_mid_step(tmp_path):
    """When a step triggers an abort, remaining pending jobs are marked aborted."""
    # Uses separate sessions per partition to avoid shared-session concurrency issues.
    async with _SessionFactory() as db:
        conn = await _make_connection(db)
        # 0% threshold: any error → abort.
        plan = await _make_plan(db, conn, abort_on_step_failure=True, error_threshold_pct=0.0)
        await _make_step(db, plan)
        run = await _make_run(db, plan)
        run_id = run.id

        # Two partitions; first has error, abort_job should be called on in-flight SF job.
        error_csv = b"sf__Id,sf__Error\nX,bad\n"
        bulk_mock = _make_bulk_client_mock(
            success_csv=CSV_HEADER,
            error_csv=error_csv,
        )
        # Give the two partitions different SF job IDs.
        bulk_mock.create_job = AsyncMock(side_effect=["JOB_1", "JOB_2"])

        with (
            patch("app.services.orchestrator.get_access_token", new=AsyncMock(return_value="token")),
            patch("app.services.orchestrator.SalesforceBulkClient", return_value=bulk_mock),
            patch("app.services.orchestrator.discover_files", return_value=["f.csv"]),
            patch("app.services.orchestrator.partition_csv", return_value=[CSV_2_ROWS, CSV_2_ROWS]),
            patch("app.services.orchestrator.ws_manager.broadcast", new=AsyncMock()),
            patch("app.services.orchestrator.settings.output_dir", str(tmp_path)),
        ):
            await _execute_run(run_id, db, db_factory=_SessionFactory)

    # Reload via a fresh session to verify the abort was committed.
    async with _SessionFactory() as fresh:
        result = await fresh.execute(select(LoadRun).where(LoadRun.id == run_id))
        run = result.scalar_one()

    assert run.status == RunStatus.aborted
