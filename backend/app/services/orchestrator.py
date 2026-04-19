"""Orchestrator facade — delegates to collaborator modules.

This module is the backward-compatible entry point for all callers and tests.
The full execution logic lives in:

- :mod:`app.services.run_coordinator`   — run lifecycle, entry points
- :mod:`app.services.step_executor`     — file discovery, partition dispatch
- :mod:`app.services.partition_executor` — per-partition SF I/O and polling
- :mod:`app.services.result_persistence` — result file download and row counting
- :mod:`app.services.run_event_publisher` — WebSocket event broadcasting

Backward-compatibility contract
--------------------------------
* ``execute_run`` and ``execute_retry_run`` remain the public entry points.
* All names that existing tests patch via
  ``patch("app.services.orchestrator.X")`` are kept as imports in *this*
  module's namespace.  The wrapper functions below pass those name-bindings as
  injectable parameters to the collaborator modules, so patches applied here
  flow through to the collaborator code at runtime.

Execution flow (unchanged from original, see spec §4.4)::

    for each step (ordered by sequence):
        1. Resolve CSV files (glob pattern match)
        2. Partition CSV files into fixed-size chunks
        3. For each partition → create a JobRecord in the DB
        4. Process all partitions concurrently (asyncio.Semaphore for concurrency)
           a. Create Bulk API job
           b. Upload CSV data
           c. Close job (trigger Salesforce processing)
           d. Poll until terminal state
           e. Download success / error / unprocessed results
           f. Persist result file paths and record counts
        5. Evaluate step success (error threshold check)
        6. If threshold exceeded and abort_on_step_failure → abort run
        7. Proceed to next step
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# ── Imports retained so that patch("app.services.orchestrator.X") calls in
# ── tests continue to replace the right name bindings.
from app.config import settings
from app.database import AsyncSessionLocal
from app.models.job import JobRecord, JobStatus
from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep
from app.services.csv_processor import partition_csv
from app.services.input_storage import get_storage
from app.services.salesforce_auth import get_access_token
from app.services.salesforce_bulk import BulkAPIError, SalesforceBulkClient, _TERMINAL_STATES
from app.utils.ws_manager import ws_manager

from app.services import partition_executor, result_persistence, run_coordinator, step_executor

logger = logging.getLogger(__name__)

_DbFactory = Callable[[], AsyncSession]


# ── Public entry points ────────────────────────────────────────────────────────


async def execute_run(run_id: str) -> None:
    """Background entry point: run a LoadRun end-to-end."""
    async with AsyncSessionLocal() as db:
        await _execute_run(run_id, db, db_factory=AsyncSessionLocal)


async def execute_retry_run(run_id: str, step_id: str, partitions: list[bytes]) -> None:
    """Background entry point: execute a retry run for a single step."""
    await run_coordinator.execute_retry_run(
        run_id,
        step_id,
        partitions,
        _get_token=get_access_token,
        _BulkClient=SalesforceBulkClient,
    )


# ── Internal wrappers (preserved for test imports and patching) ───────────────


async def _execute_run(
    run_id: str,
    db: AsyncSession,
    *,
    db_factory: _DbFactory = AsyncSessionLocal,
) -> None:
    """Orchestrate a load run.  Delegates to run_coordinator with patched bindings."""
    await run_coordinator._execute_run(
        run_id,
        db,
        db_factory=db_factory,
        _get_token=get_access_token,
        _BulkClient=SalesforceBulkClient,
        _get_storage=get_storage,
        _partition=partition_csv,
    )


async def _execute_step(
    *,
    run_id: str,
    step: LoadStep,
    plan_id: str,
    plan_name: str,
    bulk_client: SalesforceBulkClient,
    db: AsyncSession,
    semaphore: asyncio.Semaphore,
    db_factory: _DbFactory,
    output_storage,
) -> tuple[int, int]:
    """Execute one LoadStep.  Delegates to step_executor with patched bindings."""
    return await step_executor.execute_step(
        run_id=run_id,
        step=step,
        plan_id=plan_id,
        plan_name=plan_name,
        bulk_client=bulk_client,
        db=db,
        semaphore=semaphore,
        db_factory=db_factory,
        output_storage=output_storage,
        _get_storage=get_storage,
        _partition=partition_csv,
        _process=_process_partition,
    )


async def _process_partition(
    *,
    run_id: str,
    step: LoadStep,
    plan_id: str,
    plan_name: str,
    job_record_id: str,
    csv_data: bytes,
    bulk_client: SalesforceBulkClient,
    semaphore: asyncio.Semaphore,
    db_factory: _DbFactory,
    output_storage,
) -> tuple[int, int]:
    """Submit one CSV partition.  Delegates to partition_executor."""
    return await partition_executor.process_partition(
        run_id=run_id,
        step=step,
        plan_id=plan_id,
        plan_name=plan_name,
        job_record_id=job_record_id,
        csv_data=csv_data,
        bulk_client=bulk_client,
        semaphore=semaphore,
        db_factory=db_factory,
        output_storage=output_storage,
    )


async def _download_results(
    *,
    bulk_client: SalesforceBulkClient,
    sf_job_id: str,
    job_record: JobRecord,
    run_id: str,
    step_id: str,
    output_storage,
) -> tuple[int, int]:
    """Download result files.  Delegates to result_persistence."""
    return await result_persistence.download_and_persist_results(
        bulk_client=bulk_client,
        sf_job_id=sf_job_id,
        job_record=job_record,
        run_id=run_id,
        step_id=step_id,
        output_storage=output_storage,
    )


async def _abort_remaining_jobs(
    run_id: str,
    db: AsyncSession,
    bulk_client: SalesforceBulkClient,
) -> None:
    """Abort in-flight jobs.  Delegates to run_coordinator."""
    await run_coordinator._abort_remaining_jobs(run_id, db, bulk_client)


async def _mark_run_failed(
    run_id: str,
    db: AsyncSession,
    *,
    error_summary: Optional[dict] = None,
) -> None:
    """Mark run as failed.  Delegates to run_coordinator."""
    await run_coordinator._mark_run_failed(run_id, db, error_summary=error_summary)


def _count_csv_rows(csv_bytes: bytes) -> int:
    """Count data rows in a CSV.  Delegates to result_persistence."""
    return result_persistence.count_csv_rows(csv_bytes)
