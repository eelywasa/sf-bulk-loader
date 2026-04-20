"""Step-level execution: file discovery, partitioning, concurrent job dispatch.

:func:`execute_step` discovers CSV files, partitions them, creates
``JobRecord`` rows, and processes all partitions concurrently (bounded by the
run semaphore).

For query/queryAll steps a separate code path is taken: no file discovery,
no partitioning, no CSV upload.  Instead exactly **one** ``JobRecord`` is
created (``partition_index=0``) and :func:`run_bulk_query` is invoked to
execute the Salesforce Bulk API 2.0 query job and stream results to the
configured :class:`OutputStorage`.
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
from app.models.load_step import LoadStep, QUERY_OPERATIONS
from app.observability.context import input_connection_id_ctx_var, step_id_ctx_var
from app.observability.events import JobEvent, OutcomeCode, StepEvent
from app.observability.metrics import (
    record_bulk_query_job_created,
    record_bulk_query_job_failed,
    record_step_completed,
)
from app.observability import tracing
from app.services.bulk_query_executor import BulkQueryJobFailed, run_bulk_query
from app.services.csv_processor import partition_csv as _default_partition
from app.services.input_storage import InputStorageError, get_storage as _default_get_storage
from app.services.output_storage import OutputStorage
from app.services.partition_executor import process_partition
from app.services.result_persistence import _result_path, count_csv_rows
from app.services.run_event_publisher import publish_job_status_change
from app.services.salesforce_bulk import SalesforceBulkClient

logger = logging.getLogger(__name__)

_DbFactory = Callable[[], AsyncSession]


async def execute_step(
    *,
    run_id: str,
    step: LoadStep,
    plan_id: str,
    plan_name: str,
    bulk_client: SalesforceBulkClient,
    db: AsyncSession,
    semaphore: asyncio.Semaphore,
    db_factory: _DbFactory,
    output_storage: OutputStorage,
    instance_url: str = "",
    access_token: str = "",
    _get_storage: Callable = _default_get_storage,
    _partition: Callable = _default_partition,
    _process: Callable = process_partition,
    _run_bulk_query: Callable = run_bulk_query,
) -> tuple[int, int]:
    """Execute one LoadStep: discover files, partition, submit and poll jobs.

    For DML operations (insert/update/upsert/delete), CSV files are discovered,
    partitioned, and processed concurrently via the Salesforce Bulk API 2.0 DML
    endpoint.

    For query operations (query/queryAll), exactly one ``JobRecord`` is created
    and :func:`run_bulk_query` is invoked to stream query results to the output
    storage.

    Args:
        output_storage: Resolved output storage instance for writing result CSVs.
        _get_storage: Injected storage-resolver callable (default: ``get_storage``).
            Overridden by the orchestrator facade so test patches propagate.
        _partition: Injected CSV-partition callable (default: ``partition_csv``).
            Overridden by the orchestrator facade so test patches propagate.
        _process: Injected partition-processing callable.
            Overridden by the orchestrator facade for test compatibility.
        _run_bulk_query: Injected bulk-query callable (default: ``run_bulk_query``).
            Overridden in tests to avoid real Salesforce I/O.

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
        with tracing.step_span(str(step.id), step.object_name, step.operation.value) as span:
            result = await _execute_step(
                run_id=run_id,
                step=step,
                plan_id=plan_id,
                plan_name=plan_name,
                bulk_client=bulk_client,
                db=db,
                semaphore=semaphore,
                db_factory=db_factory,
                output_storage=output_storage,
                instance_url=instance_url,
                access_token=access_token,
                _get_storage=_get_storage,
                _partition=_partition,
                _process=_process,
                _run_bulk_query=_run_bulk_query,
            )
            return result
    finally:
        step_id_ctx_var.reset(_step_id_token)
        input_connection_id_ctx_var.reset(_conn_id_token)


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
    output_storage: OutputStorage,
    instance_url: str = "",
    access_token: str = "",
    _get_storage: Callable = _default_get_storage,
    _partition: Callable = _default_partition,
    _process: Callable = process_partition,
    _run_bulk_query: Callable = run_bulk_query,
) -> tuple[int, int]:
    """Inner implementation of execute_step (called after ContextVars are bound)."""
    # ── Query ops: single-job path (no glob / partition / upload) ────────────
    if step.operation in QUERY_OPERATIONS:
        return await _execute_query_step(
            run_id=run_id,
            plan_id=plan_id,
            plan_name=plan_name,
            step=step,
            instance_url=instance_url,
            access_token=access_token,
            db=db,
            output_storage=output_storage,
            _run_bulk_query=_run_bulk_query,
        )

    # ── DML path ─────────────────────────────────────────────────────────────
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
            plan_id=plan_id,
            plan_name=plan_name,
            job_record_id=jr_id,
            csv_data=csv_data,
            bulk_client=bulk_client,
            semaphore=semaphore,
            db_factory=db_factory,
            output_storage=output_storage,
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


# ── Query step executor ────────────────────────────────────────────────────────


async def _execute_query_step(
    *,
    run_id: str,
    plan_id: str,
    plan_name: str,
    step: LoadStep,
    instance_url: str,
    access_token: str,
    db: AsyncSession,
    output_storage: OutputStorage,
    _run_bulk_query: Callable = run_bulk_query,
) -> tuple[int, int]:
    """Execute a single query/queryAll step via the Bulk API 2.0 query endpoint.

    Creates exactly **one** ``JobRecord`` (``partition_index=0``) so the UI and
    observability layer see the same job-row shape as for DML steps.

    On success the ``JobRecord`` is populated with query-semantic field values:
    - ``success_file_path`` — artefact URI returned by the executor.
    - ``records_processed`` / ``total_records`` — row count returned by the executor.
    - ``records_failed`` — always 0 (query-level failures surface as job-level failure).
    - ``error_file_path`` / ``unprocessed_file_path`` — left ``None``.

    Returns:
        ``(row_count, 0)`` on success; ``(0, 1)`` on failure so the caller
        (run_coordinator) can apply the error-threshold logic uniformly.
    """
    _step_start = time.perf_counter()

    logger.info(
        "Run %s step %s: starting query step — %s %s",
        run_id,
        step.id,
        step.operation.value,
        step.object_name,
        extra={
            "event_name": StepEvent.STARTED,
            "outcome_code": None,
            "run_id": run_id,
            "step_id": str(step.id),
        },
    )
    # Metrics and span for this query step are recorded inside run_bulk_query
    # (bulk_query_executor.py) and via the wrapping step_span in execute_step.

    # ── Create the single JobRecord ────────────────────────────────────────────
    job_record = JobRecord(
        load_run_id=run_id,
        load_step_id=step.id,
        partition_index=0,
        status=JobStatus.pending,
    )
    db.add(job_record)
    await db.commit()
    await db.refresh(job_record)
    job_record_id = job_record.id

    await publish_job_status_change(
        run_id,
        step_id=step.id,
        job_id=job_record_id,
        status=JobStatus.pending.value,
    )

    # Mark in_progress
    job_record.status = JobStatus.in_progress
    job_record.started_at = datetime.now(timezone.utc)
    await db.commit()

    await publish_job_status_change(
        run_id,
        step_id=step.id,
        job_id=job_record_id,
        status=JobStatus.in_progress.value,
    )

    # ── Build the relative artefact path (SFBL-164 layout) ────────────────────
    relative_path = _result_path(
        plan_id=plan_id,
        plan_name=plan_name,
        run_id=run_id,
        step_id=step.id,
        sequence=step.sequence,
        object_name=step.object_name,
        operation=step.operation.value,
        partition_index=0,
        suffix="results",
    )

    # ── Invoke the query executor ──────────────────────────────────────────────
    try:
        query_result = await _run_bulk_query(
            soql=step.soql,
            operation=step.operation.value,
            instance_url=instance_url,
            access_token=access_token,
            output_storage=output_storage,
            relative_path=relative_path,
        )
    except BulkQueryJobFailed as exc:
        logger.warning(
            "Run %s step %s: query job reached terminal failure state %s: %s",
            run_id,
            step.id,
            exc.final_state,
            exc,
            extra={
                "event_name": JobEvent.FAILED,
                "outcome_code": OutcomeCode.SALESFORCE_API_ERROR,
                "run_id": run_id,
                "step_id": str(step.id),
                "job_record_id": job_record_id,
            },
        )
        # record_bulk_query_job_failed is also called inside run_bulk_query
        # for the job-level failure; this covers the step-level accounting.
        job_record.status = JobStatus.failed
        job_record.completed_at = datetime.now(timezone.utc)
        job_record.error_message = str(exc)
        await db.commit()

        await publish_job_status_change(
            run_id,
            step_id=step.id,
            job_id=job_record_id,
            status=JobStatus.failed.value,
        )
        record_step_completed(
            object_name=step.object_name,
            operation=step.operation.value,
            final_status="failed",
            duration_seconds=time.perf_counter() - _step_start,
            records_processed=0,
            records_succeeded=0,
            records_failed=1,
        )
        return 0, 1
    except Exception as exc:
        logger.error(
            "Run %s step %s: query step raised unexpected exception: %s",
            run_id,
            step.id,
            exc,
            extra={
                "event_name": JobEvent.FAILED,
                "outcome_code": OutcomeCode.UNEXPECTED_EXCEPTION,
                "run_id": run_id,
                "step_id": str(step.id),
                "job_record_id": job_record_id,
            },
        )
        record_bulk_query_job_failed(step.object_name, step.operation.value)  # noqa
        job_record.status = JobStatus.failed
        job_record.completed_at = datetime.now(timezone.utc)
        job_record.error_message = str(exc)
        await db.commit()

        await publish_job_status_change(
            run_id,
            step_id=step.id,
            job_id=job_record_id,
            status=JobStatus.failed.value,
        )
        record_step_completed(
            object_name=step.object_name,
            operation=step.operation.value,
            final_status="failed",
            duration_seconds=time.perf_counter() - _step_start,
            records_processed=0,
            records_succeeded=0,
            records_failed=1,
        )
        return 0, 1

    # ── Happy path: populate JobRecord with query-semantic fields ──────────────
    row_count = query_result.row_count
    job_record.status = JobStatus.job_complete
    job_record.completed_at = datetime.now(timezone.utc)
    job_record.success_file_path = query_result.artefact_uri
    job_record.records_processed = row_count
    job_record.total_records = row_count
    job_record.records_failed = 0
    job_record.error_file_path = None
    job_record.unprocessed_file_path = None
    await db.commit()

    logger.info(
        "Run %s step %s: query step completed — %d rows → %s",
        run_id,
        step.id,
        row_count,
        query_result.artefact_uri,
        extra={
            "event_name": JobEvent.COMPLETED,
            "outcome_code": OutcomeCode.OK,
            "run_id": run_id,
            "step_id": str(step.id),
            "job_record_id": job_record_id,
            "row_count": row_count,
            "artefact_uri": query_result.artefact_uri,
        },
    )

    await publish_job_status_change(
        run_id,
        step_id=step.id,
        job_id=job_record_id,
        status=JobStatus.job_complete.value,
        records_processed=row_count,
        records_failed=0,
        total_records=row_count,
    )
    record_step_completed(
        object_name=step.object_name,
        operation=step.operation.value,
        final_status="completed",
        duration_seconds=time.perf_counter() - _step_start,
        records_processed=row_count,
        records_succeeded=row_count,
        records_failed=0,
    )
    return row_count, 0
