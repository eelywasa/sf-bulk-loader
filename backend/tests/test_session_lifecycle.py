"""SFBL-408: database sessions must not outlive the work that needs them.

The 2026-10-01 outage: every partition of a large step opened a session before
waiting for a concurrency slot, so a few hundred queued partitions held a
connection each (two file descriptors on SQLite) for hours.  The process ran
out of descriptors and every new connection failed — login included — with
``sqlite3.OperationalError: unable to open database file``.

These tests run against the conftest test database, which uses the production
pooling (``NullPool``, one real connection per session).  The orchestrator
tests elsewhere share a single session across partitions, which is exactly
what would hide this defect, so they cannot cover it.
"""

from __future__ import annotations

import ast
import asyncio
import os
import pathlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, event, select, text

from app.models.connection import Connection
from app.models.job import JobRecord, JobStatus
from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep, Operation
from app.services import partition_executor, run_coordinator
from app.services.output_storage import LocalOutputStorage
from app.services.step_executor import execute_step
from tests._allure_helpers import label_layer, label_tier
from tests.conftest import _TestSession, _engine, _is_sqlite

pytestmark = [label_layer("backend"), label_tier("1a")]

N_PARTITIONS = 600
MAX_PARALLEL = 5
#: Connections allowed above MAX_PARALLEL: the probe session, plus brief
#: overlap while a finishing partition's connection closes and a newly
#: admitted one opens.
ALLOWANCE = 4

CSV_2_ROWS = b"Name,ExternalId__c\nAcme,EXT-001\nBeta,EXT-002\n"


@pytest.fixture(autouse=True)
async def _clean_tables():
    yield
    async with _TestSession() as s:
        for model in [JobRecord, LoadRun, LoadStep, LoadPlan, Connection]:
            await s.execute(delete(model))
        await s.commit()


async def _seed(db) -> tuple[LoadPlan, LoadStep, LoadRun]:
    conn = Connection(
        id=str(uuid.uuid4()),
        name="Org",
        instance_url="https://test.salesforce.com",
        login_url="https://test.salesforce.com",
        client_id="client_id",
        private_key="encrypted_key",
        username="user@example.com",
        is_sandbox=True,
    )
    plan = LoadPlan(
        id=str(uuid.uuid4()),
        connection_id=conn.id,
        name="Plan",
        max_parallel_jobs=MAX_PARALLEL,
    )
    step = LoadStep(
        id=str(uuid.uuid4()),
        load_plan_id=plan.id,
        sequence=1,
        object_name="Account",
        operation=Operation.insert,
        csv_file_pattern="accounts.csv",
        partition_size=2,
    )
    run = LoadRun(
        id=str(uuid.uuid4()),
        load_plan_id=plan.id,
        status=RunStatus.running,
        initiated_by="test",
    )
    db.add_all([conn, plan, step, run])
    await db.commit()
    return plan, step, run


class _ConnectionCounter:
    """Counts DB connections open at once, via pool connect/close events."""

    def __init__(self) -> None:
        self.open = 0
        self.peak = 0

    def _on_connect(self, *_args: object) -> None:
        self.open += 1
        self.peak = max(self.peak, self.open)

    def _on_close(self, *_args: object) -> None:
        self.open -= 1

    def __enter__(self) -> "_ConnectionCounter":
        event.listen(_engine.sync_engine, "connect", self._on_connect)
        event.listen(_engine.sync_engine, "close", self._on_close)
        return self

    def __exit__(self, *_exc: object) -> None:
        event.remove(_engine.sync_engine, "connect", self._on_connect)
        event.remove(_engine.sync_engine, "close", self._on_close)


class _LowFdLimit:
    """Lower the soft open-files limit to the current count plus *headroom*.

    Makes the test reproduce the production failure (fd exhaustion) rather
    than depend on whatever limit the CI machine happens to have.  A no-op
    where the platform can't report or set it.
    """

    def __init__(self, headroom: int) -> None:
        self._headroom = headroom
        self._saved: tuple[int, int] | None = None

    def __enter__(self) -> None:
        try:
            import resource

            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            in_use = len(os.listdir("/dev/fd"))
        except (ImportError, OSError, ValueError):
            return
        target = in_use + self._headroom
        if hard != resource.RLIM_INFINITY:
            target = min(target, hard)
        if target < soft or soft == resource.RLIM_INFINITY:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            self._saved = (soft, hard)

    def __exit__(self, *_exc: object) -> None:
        if self._saved is not None:
            import resource

            resource.setrlimit(resource.RLIMIT_NOFILE, self._saved)


async def test_queued_partitions_hold_no_db_connection(tmp_path):
    """600 partitions, 5 slots: only the active partitions hold connections.

    Falsification: with the session opened before the semaphore (the
    pre-SFBL-408 code) every queued partition holds one, so the peak is ~600,
    the lowered fd limit is exhausted, and partitions and the probe fail with
    ``unable to open database file``.
    """
    async with _TestSession() as run_db:
        plan, step, run = await _seed(run_db)

        run_db_in_transaction: list[bool] = []

        async def create_job(*_args: object, **_kwargs: object) -> str:
            # Sampled from inside the gather: the run-level session must not
            # be sitting in a transaction while partitions execute.
            run_db_in_transaction.append(run_db.in_transaction())
            await asyncio.sleep(0)
            return f"JOB{uuid.uuid4().hex[:8]}"

        bulk = MagicMock()
        bulk.create_job = AsyncMock(side_effect=create_job)
        bulk.upload_csv = AsyncMock(return_value=None)
        bulk.close_job = AsyncMock(return_value=None)
        bulk.poll_job_once = AsyncMock(return_value=("JobComplete", 2, 0, {"state": "JobComplete"}))
        bulk.get_success_results = AsyncMock(return_value=CSV_2_ROWS)
        bulk.get_failed_results = AsyncMock(return_value=b"Name,ExternalId__c\n")
        bulk.get_unprocessed_results = AsyncMock(return_value=b"Name,ExternalId__c\n")

        storage = MagicMock()
        storage.provider = "local"
        storage.discover_files.return_value = ["accounts.csv"]

        probe_ok = 0
        probe_errors: list[str] = []
        done = asyncio.Event()

        async def probe() -> None:
            # Stands in for a login request arriving mid-run.
            nonlocal probe_ok
            while not done.is_set():
                try:
                    async with _TestSession() as s:
                        await s.execute(text("SELECT 1"))
                    probe_ok += 1
                except Exception as exc:  # noqa: BLE001
                    probe_errors.append(f"{type(exc).__name__}: {exc}")
                await asyncio.sleep(0.005)

        with (
            _ConnectionCounter() as counter,
            _LowFdLimit(headroom=256 if _is_sqlite else 10_000),
            patch("app.services.run_event_publisher.ws_manager.broadcast", new=AsyncMock()),
        ):
            probe_task = asyncio.create_task(probe())
            try:
                success, errors = await execute_step(
                    run_id=run.id,
                    step=step,
                    plan=plan,
                    plan_id=plan.id,
                    plan_name=plan.name,
                    bulk_client=bulk,
                    db=run_db,
                    semaphore=asyncio.Semaphore(MAX_PARALLEL),
                    db_factory=_TestSession,
                    output_storage=LocalOutputStorage(str(tmp_path)),
                    _get_storage=AsyncMock(return_value=storage),
                    _partition=lambda _fh, _size: iter([CSV_2_ROWS] * N_PARTITIONS),
                )
            finally:
                done.set()
                await probe_task

    assert counter.peak <= MAX_PARALLEL + ALLOWANCE, (
        f"{counter.peak} DB connections open at once for {MAX_PARALLEL} slots"
    )
    assert not probe_errors, probe_errors[:3]
    assert probe_ok > 0
    assert run_db_in_transaction and not any(run_db_in_transaction)

    async with _TestSession() as s:
        statuses = (
            await s.execute(select(JobRecord.status).where(JobRecord.load_run_id == run.id))
        ).scalars().all()
    assert len(statuses) == N_PARTITIONS
    assert set(statuses) == {JobStatus.job_complete}
    assert (success, errors) == (2 * N_PARTITIONS, 0)


def test_retry_runs_partitions_through_the_same_executor():
    """The retry path shares process_partition, so it shares the fix."""
    assert run_coordinator._default_process is partition_executor.process_partition


def test_run_start_request_session_closes_before_the_run_runs(auth_client):
    """The run executes as a BackgroundTask; the request's session must
    already be closed by then.

    Falsification: under FastAPI's default "request" dependency scope the
    session closes only after BackgroundTasks finish — i.e. after the whole
    run — so the background task observes it still open.
    """
    from app.database import get_db
    from app.main import app
    import app.services.orchestrator as orchestrator

    state = {"open_sessions": 0, "open_when_run_started": None}

    async def tracking_get_db():
        async with _TestSession() as session:
            state["open_sessions"] += 1
            try:
                yield session
            finally:
                state["open_sessions"] -= 1

    async def recording_execute_run(_run_id: str) -> None:
        state["open_when_run_started"] = state["open_sessions"]

    conn_id = auth_client.post(
        "/api/connections/",
        json={
            "name": "Org",
            "instance_url": "https://myorg.my.salesforce.com",
            "login_url": "https://login.salesforce.com",
            "client_id": "cid",
            "private_key": "-----BEGIN RSA PRIVATE KEY-----\nFAKEKEY\n-----END RSA PRIVATE KEY-----",
            "username": "u@example.com",
            "is_sandbox": False,
        },
    ).json()["id"]
    plan_id = auth_client.post(
        "/api/load-plans/", json={"name": "P", "connection_id": conn_id}
    ).json()["id"]

    # auth_client restores both on teardown.
    app.dependency_overrides[get_db] = tracking_get_db
    orchestrator.execute_run = recording_execute_run

    resp = auth_client.post(f"/api/load-plans/{plan_id}/run")

    assert resp.status_code == 201
    assert state["open_when_run_started"] == 0


def test_every_get_db_dependency_is_function_scoped():
    """Every ``Depends(get_db)`` in the app must pass ``scope="function"``.

    FastAPI caches dependencies per scope, so one site left on the default
    would open a second session per request — and that one stays pinned
    until BackgroundTasks finish.
    """
    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for source in sorted(app_dir.rglob("*.py")):
        tree = ast.parse(source.read_text(), filename=str(source))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            first = node.args[0]
            if name != "Depends" or not (isinstance(first, ast.Name) and first.id == "get_db"):
                continue
            scope = next((kw.value for kw in node.keywords if kw.arg == "scope"), None)
            if not (isinstance(scope, ast.Constant) and scope.value == "function"):
                offenders.append(f"{source.relative_to(app_dir.parent)}:{node.lineno}")
    assert not offenders, f'Depends(get_db) without scope="function" at: {offenders}'
