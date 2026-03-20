"""Tests for run_coordinator.execute_retry_run orchestration.

Strategy mirrors test_orchestrator.py:
- Module-level in-process SQLite DB (test_retry_run.db) with _SessionFactory
- DB helpers to create Connection, LoadPlan, LoadStep, LoadRun
- patch app.services.run_coordinator.AsyncSessionLocal → _SessionFactory
- patch app.services.run_coordinator._default_process → AsyncMock
- patch app.utils.ws_manager.ws_manager.broadcast → AsyncMock
- pass _get_token and _BulkClient as kwargs to execute_retry_run
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
from app.services.run_coordinator import execute_retry_run

# ── In-process test database ──────────────────────────────────────────────────

_TEST_URL = "sqlite+aiosqlite:///./test_retry_run.db"
_engine = create_async_engine(_TEST_URL, echo=False)
_SessionFactory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="module", autouse=True)
async def _create_tables():
    from app.database import Base

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await _engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_tables():
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


# ── DB helpers ─────────────────────────────────────────────────────────────────


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
    max_parallel_jobs: int = 3,
) -> LoadPlan:
    plan = LoadPlan(
        id=str(uuid.uuid4()),
        connection_id=connection.id,
        name="Test Plan",
        abort_on_step_failure=True,
        error_threshold_pct=10.0,
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
) -> LoadStep:
    step = LoadStep(
        id=str(uuid.uuid4()),
        load_plan_id=plan.id,
        sequence=sequence,
        object_name=object_name,
        operation=operation,
        csv_file_pattern="accounts_*.csv",
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
        retry_of_run_id=None,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


# ── Bulk client factory ────────────────────────────────────────────────────────


def _bulk_mock() -> MagicMock:
    """AsyncContextManager MagicMock that does nothing (partitions are mocked)."""
    mock = MagicMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


def _make_bulk_cls(mock=None) -> type:
    m = mock or _bulk_mock()
    return lambda *a, **kw: m


# ── Tests ──────────────────────────────────────────────────────────────────────


async def test_execute_retry_run_happy_path_completes(db: AsyncSession):
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    step = await _make_step(db, plan)
    run = await _make_run(db, plan)

    partitions = [b"Name,ExternalId__c\nAcme Corp,EXT-001\nBeta,EXT-002\n"]

    with (
        patch("app.services.run_coordinator.AsyncSessionLocal", new=_SessionFactory),
        patch("app.services.run_coordinator._default_process", new=AsyncMock(return_value=(2, 0))),
        patch("app.utils.ws_manager.ws_manager.broadcast", new=AsyncMock()),
    ):
        await execute_retry_run(
            run.id, step.id, partitions,
            _get_token=AsyncMock(return_value="token"),
            _BulkClient=_make_bulk_cls(),
        )

    async with _SessionFactory() as fresh:
        refreshed = await fresh.get(LoadRun, run.id)
        assert refreshed.status == RunStatus.completed
        assert refreshed.total_success == 2
        assert refreshed.total_errors == 0
        assert refreshed.total_records == 2
        assert refreshed.completed_at is not None

        result = await fresh.execute(
            select(JobRecord).where(JobRecord.load_run_id == run.id)
        )
        jobs = list(result.scalars().all())
        assert len(jobs) == 1
        assert jobs[0].load_run_id == run.id


async def test_execute_retry_run_errors_yield_completed_with_errors(db: AsyncSession):
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    step = await _make_step(db, plan)
    run = await _make_run(db, plan)

    partitions = [b"Name\nBad Record\n"]

    with (
        patch("app.services.run_coordinator.AsyncSessionLocal", new=_SessionFactory),
        patch("app.services.run_coordinator._default_process", new=AsyncMock(return_value=(0, 2))),
        patch("app.utils.ws_manager.ws_manager.broadcast", new=AsyncMock()),
    ):
        await execute_retry_run(
            run.id, step.id, partitions,
            _get_token=AsyncMock(return_value="token"),
            _BulkClient=_make_bulk_cls(),
        )

    async with _SessionFactory() as fresh:
        refreshed = await fresh.get(LoadRun, run.id)
        assert refreshed.status == RunStatus.completed_with_errors
        assert refreshed.total_errors == 2


async def test_execute_retry_run_creates_one_job_per_partition(db: AsyncSession):
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    step = await _make_step(db, plan)
    run = await _make_run(db, plan)

    partitions = [b"Name\nAcme\n", b"Name\nBeta\n"]
    process_mock = AsyncMock(return_value=(1, 0))

    with (
        patch("app.services.run_coordinator.AsyncSessionLocal", new=_SessionFactory),
        patch("app.services.run_coordinator._default_process", new=process_mock),
        patch("app.utils.ws_manager.ws_manager.broadcast", new=AsyncMock()),
    ):
        await execute_retry_run(
            run.id, step.id, partitions,
            _get_token=AsyncMock(return_value="token"),
            _BulkClient=_make_bulk_cls(),
        )

    assert process_mock.await_count == 2

    async with _SessionFactory() as fresh:
        result = await fresh.execute(
            select(JobRecord).where(JobRecord.load_run_id == run.id)
        )
        assert len(list(result.scalars().all())) == 2


async def test_execute_retry_run_auth_failure_marks_run_failed(db: AsyncSession):
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    step = await _make_step(db, plan)
    run = await _make_run(db, plan)

    with (
        patch("app.services.run_coordinator.AsyncSessionLocal", new=_SessionFactory),
        patch("app.services.run_coordinator._default_process", new=AsyncMock()),
        patch("app.utils.ws_manager.ws_manager.broadcast", new=AsyncMock()),
    ):
        await execute_retry_run(
            run.id, step.id, [b"Name\nAcme\n"],
            _get_token=AsyncMock(side_effect=Exception("bad key")),
            _BulkClient=_make_bulk_cls(),
        )

    async with _SessionFactory() as fresh:
        refreshed = await fresh.get(LoadRun, run.id)
        assert refreshed.status == RunStatus.failed


async def test_execute_retry_run_run_not_found_returns_gracefully(db: AsyncSession):
    """execute_retry_run with nonexistent run_id returns without raising."""
    with (
        patch("app.services.run_coordinator.AsyncSessionLocal", new=_SessionFactory),
        patch("app.services.run_coordinator._default_process", new=AsyncMock()),
        patch("app.utils.ws_manager.ws_manager.broadcast", new=AsyncMock()),
    ):
        # Should not raise
        await execute_retry_run(
            "nonexistent-run-id", "nonexistent-step-id", [b"Name\nAcme\n"],
            _get_token=AsyncMock(return_value="token"),
            _BulkClient=_make_bulk_cls(),
        )


async def test_execute_retry_run_step_not_found_marks_run_failed(db: AsyncSession):
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    run = await _make_run(db, plan)
    # step_id does not exist in DB

    with (
        patch("app.services.run_coordinator.AsyncSessionLocal", new=_SessionFactory),
        patch("app.services.run_coordinator._default_process", new=AsyncMock()),
        patch("app.utils.ws_manager.ws_manager.broadcast", new=AsyncMock()),
    ):
        await execute_retry_run(
            run.id, "nonexistent-step-id", [b"Name\nAcme\n"],
            _get_token=AsyncMock(return_value="token"),
            _BulkClient=_make_bulk_cls(),
        )

    async with _SessionFactory() as fresh:
        refreshed = await fresh.get(LoadRun, run.id)
        assert refreshed.status == RunStatus.failed


async def test_execute_retry_run_exception_in_gather_counted_as_zero(db: AsyncSession):
    """An exception from one partition is swallowed by gather; other partitions still count."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    step = await _make_step(db, plan)
    run = await _make_run(db, plan)

    # First partition raises, second succeeds
    process_mock = AsyncMock(
        side_effect=[Exception("partition exploded"), (1, 0)]
    )

    with (
        patch("app.services.run_coordinator.AsyncSessionLocal", new=_SessionFactory),
        patch("app.services.run_coordinator._default_process", new=process_mock),
        patch("app.utils.ws_manager.ws_manager.broadcast", new=AsyncMock()),
    ):
        await execute_retry_run(
            run.id, step.id, [b"Name\nAcme\n", b"Name\nBeta\n"],
            _get_token=AsyncMock(return_value="token"),
            _BulkClient=_make_bulk_cls(),
        )

    async with _SessionFactory() as fresh:
        refreshed = await fresh.get(LoadRun, run.id)
        # Zero errors from the successful partition → completed (not completed_with_errors)
        assert refreshed.status == RunStatus.completed
        assert refreshed.total_success == 1
        assert refreshed.total_errors == 0


async def test_execute_retry_run_respects_semaphore():
    """Semaphore prevents more than max_parallel_jobs running concurrently."""
    async with _SessionFactory() as db:
        conn = await _make_connection(db)
        plan = await _make_plan(db, conn, max_parallel_jobs=2)
        step = await _make_step(db, plan)
        run = await _make_run(db, plan)

    concurrent_high_water = 0
    current_concurrent = 0

    async def tracking_process(**kwargs):
        # Must acquire the passed semaphore to actually test its effect
        async with kwargs['semaphore']:
            nonlocal concurrent_high_water, current_concurrent
            current_concurrent += 1
            concurrent_high_water = max(concurrent_high_water, current_concurrent)
            await asyncio.sleep(0)
            current_concurrent -= 1
        return (1, 0)

    # 4 partitions, semaphore should limit to 2 at once
    partitions = [b"Name\nA\n", b"Name\nB\n", b"Name\nC\n", b"Name\nD\n"]

    with (
        patch("app.services.run_coordinator.AsyncSessionLocal", new=_SessionFactory),
        patch("app.services.run_coordinator._default_process", new=tracking_process),
        patch("app.utils.ws_manager.ws_manager.broadcast", new=AsyncMock()),
    ):
        await execute_retry_run(
            run.id, step.id, partitions,
            _get_token=AsyncMock(return_value="token"),
            _BulkClient=_make_bulk_cls(),
        )

    assert concurrent_high_water <= 2, (
        f"Expected at most 2 concurrent partitions, got {concurrent_high_water}"
    )


async def test_execute_retry_run_broadcasts_run_started_and_completed(db: AsyncSession):
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    step = await _make_step(db, plan)
    run = await _make_run(db, plan)

    events: list[dict] = []

    async def capture_broadcast(run_id, event):
        events.append(event)

    with (
        patch("app.services.run_coordinator.AsyncSessionLocal", new=_SessionFactory),
        patch("app.services.run_coordinator._default_process", new=AsyncMock(return_value=(1, 0))),
        patch("app.utils.ws_manager.ws_manager.broadcast", side_effect=capture_broadcast),
    ):
        await execute_retry_run(
            run.id, step.id, [b"Name\nAcme\n"],
            _get_token=AsyncMock(return_value="token"),
            _BulkClient=_make_bulk_cls(),
        )

    event_names = [e.get("event") for e in events]
    assert "run_started" in event_names
    assert any(e in event_names for e in ("run_completed",))


async def test_execute_retry_run_transitions_pending_to_running(db: AsyncSession):
    """The run is in 'running' status while partitions are being processed."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    step = await _make_step(db, plan)
    run = await _make_run(db, plan)

    observed_statuses: list[str] = []

    async def observe_process(**kwargs):
        async with _SessionFactory() as s:
            r = await s.get(LoadRun, run.id)
            if r:
                observed_statuses.append(r.status.value)
        return (1, 0)

    with (
        patch("app.services.run_coordinator.AsyncSessionLocal", new=_SessionFactory),
        patch("app.services.run_coordinator._default_process", new=observe_process),
        patch("app.utils.ws_manager.ws_manager.broadcast", new=AsyncMock()),
    ):
        await execute_retry_run(
            run.id, step.id, [b"Name\nAcme\n"],
            _get_token=AsyncMock(return_value="token"),
            _BulkClient=_make_bulk_cls(),
        )

    assert "running" in observed_statuses


async def test_execute_retry_run_stuck_jobs_marked_failed_after_gather(db: AsyncSession):
    """Jobs left in intermediate states after gather are transitioned to failed."""
    conn = await _make_connection(db)
    plan = await _make_plan(db, conn)
    step = await _make_step(db, plan)
    run = await _make_run(db, plan)

    # process_mock raises so the job is never moved to job_complete
    # The stuck-state cleanup UPDATE should catch it
    async def stuck_process(**kwargs):
        # Don't update the job status — leave it as pending (its initial state)
        raise Exception("stuck partition")

    with (
        patch("app.services.run_coordinator.AsyncSessionLocal", new=_SessionFactory),
        patch("app.services.run_coordinator._default_process", new=stuck_process),
        patch("app.utils.ws_manager.ws_manager.broadcast", new=AsyncMock()),
    ):
        await execute_retry_run(
            run.id, step.id, [b"Name\nAcme\n"],
            _get_token=AsyncMock(return_value="token"),
            _BulkClient=_make_bulk_cls(),
        )

    async with _SessionFactory() as fresh:
        result = await fresh.execute(
            select(JobRecord).where(JobRecord.load_run_id == run.id)
        )
        jobs = list(result.scalars().all())
        assert len(jobs) == 1
        # The stuck-state cleanup should have marked it failed
        assert jobs[0].status == JobStatus.failed
