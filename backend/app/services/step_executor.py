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

from app.models.job import JobRecord, JobStatus
from app.models.load_step import LoadStep
from app.services.csv_processor import discover_files as _default_discover, partition_csv as _default_partition
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
    _discover: Callable = _default_discover,
    _partition: Callable = _default_partition,
    _process: Callable = process_partition,
) -> tuple[int, int]:
    """Execute one LoadStep: discover files, partition, submit and poll jobs.

    Args:
        _discover: Injected file-discovery callable (default: ``discover_files``).
            Overridden by the orchestrator facade so test patches propagate.
        _partition: Injected CSV-partition callable (default: ``partition_csv``).
            Overridden by the orchestrator facade so test patches propagate.
        _process: Injected partition-processing callable.
            Overridden by the orchestrator facade for test compatibility.

    Returns:
        ``(total_success, total_errors)`` record counts across all partitions.
    """
    # ── 1. Discover CSV files ─────────────────────────────────────────────────
    csv_files = _discover(step.csv_file_pattern)
    if not csv_files:
        logger.warning(
            "Run %s step %s: no files matched pattern %r — skipping",
            run_id,
            step.id,
            step.csv_file_pattern,
        )
        return 0, 0

    # ── 2. Build list of (partition_index, csv_bytes) ─────────────────────────
    partitions: list[tuple[int, bytes]] = []
    for csv_file in csv_files:
        for chunk in _partition(csv_file, step.partition_size):
            partitions.append((len(partitions), chunk))

    if not partitions:
        logger.warning(
            "Run %s step %s: files matched but yielded no data rows — skipping",
            run_id,
            step.id,
        )
        return 0, 0

    logger.info(
        "Run %s step %s: %d file(s) → %d partition(s)",
        run_id,
        step.id,
        len(csv_files),
        len(partitions),
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
            )
        elif isinstance(res, tuple):
            success, errors = res
            total_success += success
            total_errors += errors
        # None is returned on early-exit paths (abort, create_job failure, etc.)

    return total_success, total_errors
