"""Step-level execution: file discovery, partitioning, concurrent job dispatch.

:func:`execute_step` discovers CSV files, partitions them, creates
``JobRecord`` rows, and processes all partitions concurrently (bounded by the
run semaphore).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

import time

from app.models.job import JobRecord, JobStatus
from app.models.load_step import LoadStep
from app.observability.context import input_connection_id_ctx_var, step_id_ctx_var
from app.observability.events import JobEvent, OutcomeCode, StepEvent
from app.observability.metrics import record_step_completed
from app.services.csv_processor import partition_csv as _default_partition
from app.services.input_storage import InputStorageError, get_storage as _default_get_storage
from app.services.partition_executor import process_partition
from app.services.result_persistence import count_csv_rows
from app.services.salesforce_bulk import SalesforceBulkClient

logger = logging.getLogger(__name__)

_DbFactory = Callable[[], AsyncSession]


async def execute_step(
    *,
    run_id: str,
    step: LoadStep,
    bulk_client: SalesforceBulkClient,
    db: AsyncSession,
    semaphore: asyncio.Semaphore,
    db_factory: _DbFactory,
    _get_storage: Callable = _default_get_storage,
    _partition: Callable = _default_partition,
    _process: Callable = process_partition,
) -> tuple[int, int]:
    """Execute one LoadStep: discover files, partition, submit and poll jobs.

    Args:
        _get_storage: Injected storage-resolver callable (default: ``get_storage``).
            Overridden by the orchestrator facade so test patches propagate.
        _partition: Injected CSV-partition callable (default: ``partition_csv``).
            Overridden by the orchestrator facade so test patches propagate.
        _process: Injected partition-processing callable.
            Overridden by the orchestrator facade for test compatibility.

    Returns:
        ``(total_success, total_errors)`` record counts across all partitions.

    Raises:
        InputStorageError: If the storage connection cannot be resolved or
            accessed.  The caller (run_coordinator) is responsible for marking
            the run as failed.
    """
    # Bind step-scoped context so all log calls (including those in spawned
    # partition tasks) carry step_id and input_connection_id automatically.
    _step_id_token = step_id_ctx_var.set(str(step.id))
    _conn_id_token = input_connection_id_ctx_var.set(
        str(step.input_connection_id) if step.input_connection_id else None
    )

    try:
        return await _execute_step(
            run_id=run_id,
            step=step,
            bulk_client=bulk_client,
            db=db,
            semaphore=semaphore,
            db_factory=db_factory,
            _get_storage=_get_storage,
            _partition=_partition,
            _process=_process,
        )
    finally:
        step_id_ctx_var.reset(_step_id_token)
        input_connection_id_ctx_var.reset(_conn_id_token)


async def _execute_step(
    *,
    run_id: str,
    step: LoadStep,
    bulk_client: SalesforceBulkClient,
    db: AsyncSession,
    semaphore: asyncio.Semaphore,
    db_factory: _DbFactory,
    _get_storage: Callable = _default_get_storage,
    _partition: Callable = _default_partition,
    _process: Callable = process_partition,
) -> tuple[int, int]:
    """Inner implementation of execute_step (called after ContextVars are bound)."""
    # ── 1. Resolve storage and discover CSV files ─────────────────────────────
    storage = await _get_storage(step.input_connection_id, db)
    rel_paths = storage.discover_files(step.csv_file_pattern)
    if not rel_paths:
        logger.warning(
            "Run %s step %s: no files matched pattern %r on %s — skipping",
            run_id,
            step.id,
            step.csv_file_pattern,
            storage.provider,
            extra={"event_name": StepEvent.COMPLETED, "outcome_code": OutcomeCode.OK,
                   "run_id": run_id, "step_id": str(step.id)},
        )
        return 0, 0

    # ── 2. Build list of (partition_index, csv_bytes) ─────────────────────────
    partitions: list[tuple[int, bytes]] = []
    for rel_path in rel_paths:
        with storage.open_text(rel_path) as fh:
            for chunk in _partition(fh, step.partition_size):
                partitions.append((len(partitions), chunk))

    if not partitions:
        logger.warning(
            "Run %s step %s: files matched but yielded no data rows — skipping",
            run_id,
            step.id,
            extra={"event_name": StepEvent.COMPLETED, "outcome_code": OutcomeCode.OK,
                   "run_id": run_id, "step_id": str(step.id)},
        )
        return 0, 0

    _step_start = time.perf_counter()
    logger.info(
        "Run %s step %s: %d file(s) [%s] → %d partition(s)",
        run_id,
        step.id,
        len(rel_paths),
        storage.provider,
        len(partitions),
        extra={"event_name": StepEvent.STARTED, "run_id": run_id,
               "step_id": str(step.id)},
    )

    # ── 3. Create JobRecord rows ──────────────────────────────────────────────
    job_records: list[JobRecord] = []
    for partition_index, csv_data in partitions:
        jr = JobRecord(
            load_run_id=run_id,
            load_step_id=step.id,
            partition_index=partition_index,
            status=JobStatus.pending,
            total_records=count_csv_rows(csv_data),
        )
        db.add(jr)
        job_records.append(jr)
    await db.commit()
    # Refresh to get generated IDs.
    for jr in job_records:
        await db.refresh(jr)

    # Snapshot IDs now (while objects are fresh) so expire_all() below is safe.
    job_record_ids = [jr.id for jr in job_records]

    # ── 4. Process all partitions concurrently ────────────────────────────────
    tasks = [
        _process(
            run_id=run_id,
            step=step,
            job_record_id=jr_id,
            csv_data=csv_data,
            bulk_client=bulk_client,
            semaphore=semaphore,
            db_factory=db_factory,
        )
        for jr_id, (_, csv_data) in zip(job_record_ids, partitions)
    ]
    # ── 5. Aggregate partition results ────────────────────────────────────────
    gather_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Any job still in an intermediate state after gather means _process raised
    # an unexpected (non-BulkAPIError) exception and never cleaned up.
    await db.execute(
        update(JobRecord)
        .where(
            JobRecord.id.in_(job_record_ids),
            JobRecord.status.in_([
                JobStatus.pending,
                JobStatus.uploading,
                JobStatus.upload_complete,
                JobStatus.in_progress,
            ]),
        )
        .values(status=JobStatus.failed, completed_at=datetime.now(timezone.utc))
    )
    await db.commit()

    total_success = 0
    total_errors = 0
    for i, res in enumerate(gather_results):
        if isinstance(res, Exception):
            logger.error(
                "Run %s step %s partition %d: unhandled exception: %s",
                run_id,
                step.id,
                i,
                res,
                extra={"event_name": JobEvent.FAILED,
                       "outcome_code": OutcomeCode.UNEXPECTED_EXCEPTION,
                       "run_id": run_id, "step_id": str(step.id)},
            )
        elif isinstance(res, tuple):
            success, errors = res
            total_success += success
            total_errors += errors
        # None is returned on early-exit paths (abort, create_job failure, etc.)

    step_final_status = "failed" if total_errors > 0 and total_success == 0 else (
        "completed_with_errors" if total_errors > 0 else "completed"
    )
    record_step_completed(
        object_name=step.object_name,
        operation=step.operation.value,
        final_status=step_final_status,
        duration_seconds=time.perf_counter() - _step_start,
        records_processed=total_success + total_errors,
        records_succeeded=total_success,
        records_failed=total_errors,
    )
    return total_success, total_errors
