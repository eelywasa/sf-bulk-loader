"""Orchestrator — core execution engine for a Load Run (spec §4.4).

Execution flow per run::

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

Abort behaviour (spec §9.3):
    Checked at each step boundary and before each partition is submitted.
    If the run is found to be ``aborted`` in the DB (set by the REST
    endpoint), the orchestrator stops processing new steps, calls
    ``abort_job()`` on any in-flight Salesforce jobs, and marks all pending
    ``JobRecord`` rows as ``aborted``.

Concurrency model:
    Each call to :func:`_process_partition` acquires an
    ``asyncio.Semaphore`` (sized by ``LoadPlan.max_parallel_jobs``) before
    touching the Salesforce API.  Every partition creates its *own*
    ``AsyncSession`` so concurrent coroutines don't share session state.
    The main orchestration session is only used for step-level bookkeeping.
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

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.job import JobRecord, JobStatus
from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep
from app.services.csv_processor import discover_files, partition_csv
from app.services.salesforce_auth import get_access_token
from app.services.salesforce_bulk import BulkAPIError, SalesforceBulkClient
from app.utils.ws_manager import ws_manager

logger = logging.getLogger(__name__)

# Type alias for the session factory used by partitions.
# In production this is ``AsyncSessionLocal``; tests inject a different one.
_DbFactory = Callable[[], AsyncSession]


# ── Public entry point ────────────────────────────────────────────────────────


async def execute_run(run_id: str) -> None:
    """Background entry point: run a LoadRun end-to-end.

    Creates its own ``AsyncSession`` so it can be submitted as a
    ``BackgroundTask`` independently of the originating HTTP request.

    Args:
        run_id: Primary key of the :class:`~app.models.load_run.LoadRun`.
    """
    async with AsyncSessionLocal() as db:
        await _execute_run(run_id, db, db_factory=AsyncSessionLocal)


# ── Internal orchestration ────────────────────────────────────────────────────


async def _execute_run(
    run_id: str,
    db: AsyncSession,
    *,
    db_factory: _DbFactory = AsyncSessionLocal,
) -> None:
    """Orchestrate a load run.  Separated from :func:`execute_run` for testability.

    Args:
        run_id: LoadRun primary key.
        db: Session used for run/plan/step-level reads and writes.
        db_factory: Callable that returns a new ``AsyncSession`` context manager.
            Injected here so tests can substitute the test database.
    """
    # ── Load run + plan + steps + connection ──────────────────────────────────
    result = await db.execute(
        select(LoadRun)
        .where(LoadRun.id == run_id)
        .options(
            selectinload(LoadRun.load_plan).selectinload(LoadPlan.load_steps),
            selectinload(LoadRun.load_plan).selectinload(LoadPlan.connection),
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        logger.error("execute_run: LoadRun %s not found", run_id)
        return

    plan: LoadPlan = run.load_plan
    steps: list[LoadStep] = sorted(plan.load_steps, key=lambda s: s.sequence)

    # ── Mark run as running ───────────────────────────────────────────────────
    run.status = RunStatus.running
    run.started_at = datetime.now(timezone.utc)
    await db.commit()

    await ws_manager.broadcast(run_id, {"event": "run_started", "run_id": run_id})
    logger.info(
        "Run %s started: plan=%s steps=%d max_parallel_jobs=%d",
        run_id,
        plan.id,
        len(steps),
        plan.max_parallel_jobs,
    )

    # ── Obtain Salesforce access token ────────────────────────────────────────
    try:
        access_token = await get_access_token(db, plan.connection)
    except Exception as exc:
        logger.error("Run %s: failed to obtain access token: %s", run_id, exc)
        await _mark_run_failed(run_id, db, error_summary={"auth_error": str(exc)})
        await ws_manager.broadcast(
            run_id, {"event": "run_failed", "run_id": run_id, "error": str(exc)}
        )
        return

    semaphore = asyncio.Semaphore(plan.max_parallel_jobs)
    run_total_records = 0
    run_total_success = 0
    run_total_errors = 0
    any_step_failed = False

    # ── Execute steps in sequence ─────────────────────────────────────────────
    async with SalesforceBulkClient(
        plan.connection.instance_url, access_token
    ) as bulk_client:
        for step in steps:
            # Reload run to detect external abort before starting next step.
            await db.refresh(run)
            if run.status == RunStatus.aborted:
                logger.info("Run %s: aborted before step %s", run_id, step.id)
                await ws_manager.broadcast(run_id, {"event": "run_aborted", "run_id": run_id})
                return

            await ws_manager.broadcast(
                run_id,
                {
                    "event": "step_started",
                    "run_id": run_id,
                    "step_id": step.id,
                    "object_name": step.object_name,
                    "sequence": step.sequence,
                },
            )
            logger.info(
                "Run %s: starting step %d — %s %s",
                run_id,
                step.sequence,
                step.operation.value,
                step.object_name,
            )

            step_success, step_errors = await _execute_step(
                run_id=run_id,
                step=step,
                bulk_client=bulk_client,
                db=db,
                semaphore=semaphore,
                db_factory=db_factory,
            )

            total_step_records = step_success + step_errors
            run_total_records += total_step_records
            run_total_success += step_success
            run_total_errors += step_errors

            error_rate = (
                step_errors / total_step_records if total_step_records > 0 else 0.0
            )
            threshold = plan.error_threshold_pct / 100.0
            step_failed = error_rate > threshold

            await ws_manager.broadcast(
                run_id,
                {
                    "event": "step_completed",
                    "run_id": run_id,
                    "step_id": step.id,
                    "object_name": step.object_name,
                    "records_processed": total_step_records,
                    "records_success": step_success,
                    "records_failed": step_errors,
                    "step_failed": step_failed,
                },
            )

            if step_failed:
                any_step_failed = True
                logger.warning(
                    "Run %s step %s exceeded error threshold: %.1f%% > %.1f%%",
                    run_id,
                    step.id,
                    error_rate * 100,
                    plan.error_threshold_pct,
                )
                if plan.abort_on_step_failure:
                    logger.error(
                        "Run %s: aborting after step failure (abort_on_step_failure=True)",
                        run_id,
                    )
                    await _abort_remaining_jobs(run_id, db, bulk_client)
                    run.status = RunStatus.aborted
                    run.completed_at = datetime.now(timezone.utc)
                    run.total_records = run_total_records
                    run.total_success = run_total_success
                    run.total_errors = run_total_errors
                    await db.commit()
                    await ws_manager.broadcast(
                        run_id,
                        {
                            "event": "run_aborted",
                            "run_id": run_id,
                            "reason": "step_failure_threshold",
                        },
                    )
                    return

    # ── Finalise run ──────────────────────────────────────────────────────────
    final_status = (
        RunStatus.completed_with_errors if any_step_failed else RunStatus.completed
    )
    run.status = final_status
    run.completed_at = datetime.now(timezone.utc)
    run.total_records = run_total_records
    run.total_success = run_total_success
    run.total_errors = run_total_errors
    await db.commit()

    await ws_manager.broadcast(
        run_id,
        {
            "event": "run_completed",
            "run_id": run_id,
            "status": final_status.value,
            "total_records": run_total_records,
            "total_success": run_total_success,
            "total_errors": run_total_errors,
        },
    )
    logger.info(
        "Run %s completed (%s): records=%d success=%d errors=%d",
        run_id,
        final_status.value,
        run_total_records,
        run_total_success,
        run_total_errors,
    )


async def _execute_step(
    *,
    run_id: str,
    step: LoadStep,
    bulk_client: SalesforceBulkClient,
    db: AsyncSession,
    semaphore: asyncio.Semaphore,
    db_factory: _DbFactory,
) -> tuple[int, int]:
    """Execute one LoadStep: discover files, partition, submit and poll jobs.

    Returns:
        ``(total_success, total_errors)`` record counts across all partitions.
    """
    # ── 1. Discover CSV files ─────────────────────────────────────────────────
    csv_files = discover_files(step.csv_file_pattern)
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
        for chunk in partition_csv(csv_file, step.partition_size):
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
    for partition_index, _ in partitions:
        jr = JobRecord(
            load_run_id=run_id,
            load_step_id=step.id,
            partition_index=partition_index,
            status=JobStatus.pending,
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
        _process_partition(
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
    # Each partition returns (success_count, error_count) on success, or None/
    # Exception on failure.  Counts come directly from the partition so we
    # don't need to re-query the DB (which would require session expiry tricks).
    gather_results = await asyncio.gather(*tasks, return_exceptions=True)
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
        # — those partitions contribute 0 to the counts.

    return total_success, total_errors


async def _process_partition(
    *,
    run_id: str,
    step: LoadStep,
    job_record_id: str,
    csv_data: bytes,
    bulk_client: SalesforceBulkClient,
    semaphore: asyncio.Semaphore,
    db_factory: _DbFactory,
) -> tuple[int, int]:
    """Submit one CSV partition as a Bulk API 2.0 job and download results.

    Each call creates its own ``AsyncSession`` to avoid shared session state
    across concurrent coroutines.  The semaphore limits the number of
    partitions that may communicate with Salesforce at the same time.

    Returns:
        ``(success_count, error_count)`` — 0, 0 on early-exit failure paths.
    """
    async with db_factory() as db:
        job_rec = await db.get(JobRecord, job_record_id)
        if job_rec is None:
            logger.error("_process_partition: JobRecord %s not found", job_record_id)
            return 0, 0

        async with semaphore:
            # Check for external abort before submitting to Salesforce.
            run_check = await db.get(LoadRun, run_id)
            if run_check and run_check.status == RunStatus.aborted:
                job_rec.status = JobStatus.aborted
                await db.commit()
                return 0, 0

            # ── Create SF job ─────────────────────────────────────────────────
            job_rec.status = JobStatus.uploading
            job_rec.started_at = datetime.now(timezone.utc)
            await db.commit()

            try:
                sf_job_id = await bulk_client.create_job(
                    step.object_name,
                    step.operation.value,
                    external_id_field=step.external_id_field or None,
                    assignment_rule_id=step.assignment_rule_id or None,
                )
            except BulkAPIError as exc:
                logger.error(
                    "Run %s step %s partition %d: create_job failed: %s",
                    run_id,
                    step.id,
                    job_rec.partition_index,
                    exc,
                )
                job_rec.status = JobStatus.failed
                job_rec.error_message = str(exc)
                job_rec.completed_at = datetime.now(timezone.utc)
                await db.commit()
                await ws_manager.broadcast(
                    run_id,
                    {
                        "event": "job_status_change",
                        "run_id": run_id,
                        "step_id": step.id,
                        "job_id": job_record_id,
                        "status": JobStatus.failed.value,
                    },
                )
                return 0, 0

            job_rec.sf_job_id = sf_job_id
            await db.commit()
            await ws_manager.broadcast(
                run_id,
                {
                    "event": "job_status_change",
                    "run_id": run_id,
                    "step_id": step.id,
                    "job_id": job_record_id,
                    "sf_job_id": sf_job_id,
                    "status": JobStatus.uploading.value,
                },
            )

            # ── Upload CSV ────────────────────────────────────────────────────
            try:
                await bulk_client.upload_csv(sf_job_id, csv_data)
                await bulk_client.close_job(sf_job_id)
            except BulkAPIError as exc:
                logger.error(
                    "Run %s step %s partition %d: upload/close failed: %s",
                    run_id,
                    step.id,
                    job_rec.partition_index,
                    exc,
                )
                job_rec.status = JobStatus.failed
                job_rec.error_message = str(exc)
                job_rec.completed_at = datetime.now(timezone.utc)
                await db.commit()
                await ws_manager.broadcast(
                    run_id,
                    {
                        "event": "job_status_change",
                        "run_id": run_id,
                        "step_id": step.id,
                        "job_id": job_record_id,
                        "sf_job_id": sf_job_id,
                        "status": JobStatus.failed.value,
                    },
                )
                return 0, 0

            job_rec.status = JobStatus.upload_complete
            await db.commit()
            await ws_manager.broadcast(
                run_id,
                {
                    "event": "job_status_change",
                    "run_id": run_id,
                    "step_id": step.id,
                    "job_id": job_record_id,
                    "sf_job_id": sf_job_id,
                    "status": JobStatus.upload_complete.value,
                },
            )

            # ── Poll job ──────────────────────────────────────────────────────
            job_rec.status = JobStatus.in_progress
            await db.commit()

            try:
                terminal_state = await _poll_with_timeout(
                    bulk_client=bulk_client,
                    sf_job_id=sf_job_id,
                    run_id=run_id,
                    step_id=step.id,
                    partition_index=job_rec.partition_index,
                )
            except BulkAPIError as exc:
                logger.error(
                    "Run %s step %s partition %d: polling failed: %s",
                    run_id,
                    step.id,
                    job_rec.partition_index,
                    exc,
                )
                job_rec.status = JobStatus.failed
                job_rec.error_message = str(exc)
                job_rec.completed_at = datetime.now(timezone.utc)
                await db.commit()
                await ws_manager.broadcast(
                    run_id,
                    {
                        "event": "job_status_change",
                        "run_id": run_id,
                        "step_id": step.id,
                        "job_id": job_record_id,
                        "sf_job_id": sf_job_id,
                        "status": JobStatus.failed.value,
                    },
                )
                return 0, 0

            # ── Download results ──────────────────────────────────────────────
            records_processed, records_failed = await _download_results(
                bulk_client=bulk_client,
                sf_job_id=sf_job_id,
                job_record=job_rec,
                run_id=run_id,
                step_id=step.id,
            )

            status_map = {
                "JobComplete": JobStatus.job_complete,
                "Failed": JobStatus.failed,
                "Aborted": JobStatus.aborted,
            }
            final_status = status_map.get(terminal_state, JobStatus.failed)

            job_rec.status = final_status
            job_rec.records_processed = records_processed
            job_rec.records_failed = records_failed
            job_rec.completed_at = datetime.now(timezone.utc)
            await db.commit()

            await ws_manager.broadcast(
                run_id,
                {
                    "event": "job_status_change",
                    "run_id": run_id,
                    "step_id": step.id,
                    "job_id": job_record_id,
                    "sf_job_id": sf_job_id,
                    "status": final_status.value,
                    "records_processed": records_processed,
                    "records_failed": records_failed,
                },
            )
            logger.info(
                "Run %s step %s partition %d: %s (processed=%d, failed=%d)",
                run_id,
                step.id,
                job_rec.partition_index,
                terminal_state,
                records_processed,
                records_failed,
            )
            return records_processed - records_failed, records_failed


# ── Polling helper ────────────────────────────────────────────────────────────


async def _poll_with_timeout(
    *,
    bulk_client: SalesforceBulkClient,
    sf_job_id: str,
    run_id: str,
    step_id: str,
    partition_index: int,
) -> str:
    """Poll a Bulk API job, emitting a warning if the timeout is exceeded.

    Per spec §9.1: if the job is still ``InProgress`` after
    ``SF_JOB_TIMEOUT_MINUTES``, log a warning and *continue* polling — do
    not abort the job.

    Returns:
        Terminal Salesforce state string (``"JobComplete"``, ``"Failed"``, or
        ``"Aborted"``).

    Raises:
        BulkAPIError: If polling fails permanently.
    """
    timeout_s = settings.sf_job_timeout_minutes * 60
    try:
        return await asyncio.wait_for(
            bulk_client.poll_job(sf_job_id), timeout=float(timeout_s)
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Run %s step %s partition %d: job %s still in progress after %d min "
            "— continuing to poll (spec §9.1)",
            run_id,
            step_id,
            partition_index,
            sf_job_id,
            settings.sf_job_timeout_minutes,
        )
        # Continue polling without a second timeout (spec: log warning and continue).
        return await bulk_client.poll_job(sf_job_id)


# ── Results download helper ───────────────────────────────────────────────────


async def _download_results(
    *,
    bulk_client: SalesforceBulkClient,
    sf_job_id: str,
    job_record: JobRecord,
    run_id: str,
    step_id: str,
) -> tuple[int, int]:
    """Download success / error / unprocessed CSVs and persist them locally.

    Files are saved under ``{OUTPUT_DIR}/{run_id}/{step_id}/`` using relative
    paths stored in the DB (relative to ``OUTPUT_DIR``).

    Returns:
        ``(records_processed, records_failed)`` where *records_processed*
        includes both successes and failures.
    """
    output_base = pathlib.Path(settings.output_dir) / run_id / step_id
    output_base.mkdir(parents=True, exist_ok=True)

    idx = job_record.partition_index
    records_processed = 0
    records_failed = 0

    # ── Success results ───────────────────────────────────────────────────────
    try:
        success_csv = await bulk_client.get_success_results(sf_job_id)
        if success_csv:
            rel = str(pathlib.Path(run_id) / step_id / f"partition_{idx}_success.csv")
            (pathlib.Path(settings.output_dir) / rel).write_bytes(success_csv)
            job_record.success_file_path = rel
            records_processed += _count_csv_rows(success_csv)
    except BulkAPIError as exc:
        logger.warning(
            "Run %s partition %d: could not download success results for job %s: %s",
            run_id,
            idx,
            sf_job_id,
            exc,
        )

    # ── Error results ─────────────────────────────────────────────────────────
    try:
        error_csv = await bulk_client.get_failed_results(sf_job_id)
        if error_csv:
            rel = str(pathlib.Path(run_id) / step_id / f"partition_{idx}_errors.csv")
            (pathlib.Path(settings.output_dir) / rel).write_bytes(error_csv)
            job_record.error_file_path = rel
            error_count = _count_csv_rows(error_csv)
            records_failed += error_count
            records_processed += error_count
    except BulkAPIError as exc:
        logger.warning(
            "Run %s partition %d: could not download error results for job %s: %s",
            run_id,
            idx,
            sf_job_id,
            exc,
        )

    # ── Unprocessed results ───────────────────────────────────────────────────
    try:
        unprocessed_csv = await bulk_client.get_unprocessed_results(sf_job_id)
        if unprocessed_csv:
            rel = str(
                pathlib.Path(run_id) / step_id / f"partition_{idx}_unprocessed.csv"
            )
            (pathlib.Path(settings.output_dir) / rel).write_bytes(unprocessed_csv)
            job_record.unprocessed_file_path = rel
    except BulkAPIError as exc:
        logger.warning(
            "Run %s partition %d: could not download unprocessed results for job %s: %s",
            run_id,
            idx,
            sf_job_id,
            exc,
        )

    return records_processed, records_failed


# ── Abort helper ──────────────────────────────────────────────────────────────


async def _abort_remaining_jobs(
    run_id: str,
    db: AsyncSession,
    bulk_client: SalesforceBulkClient,
) -> None:
    """Call ``abort_job()`` on any in-flight SF jobs; mark pending ones aborted."""
    result = await db.execute(
        select(JobRecord).where(
            JobRecord.load_run_id == run_id,
            JobRecord.status.in_(
                [
                    JobStatus.uploading,
                    JobStatus.upload_complete,
                    JobStatus.in_progress,
                ]
            ),
        )
    )
    active_jobs = list(result.scalars().all())

    for job in active_jobs:
        if job.sf_job_id:
            try:
                await bulk_client.abort_job(job.sf_job_id)
            except BulkAPIError as exc:
                logger.warning(
                    "Run %s: could not abort SF job %s: %s",
                    run_id,
                    job.sf_job_id,
                    exc,
                )
        job.status = JobStatus.aborted
        job.completed_at = datetime.now(timezone.utc)

    # Mark pending jobs aborted (not yet submitted to Salesforce).
    await db.execute(
        update(JobRecord)
        .where(
            JobRecord.load_run_id == run_id,
            JobRecord.status == JobStatus.pending,
        )
        .values(status=JobStatus.aborted)
    )
    await db.commit()


# ── Failure helper ────────────────────────────────────────────────────────────


async def _mark_run_failed(
    run_id: str,
    db: AsyncSession,
    *,
    error_summary: Optional[dict] = None,
) -> None:
    """Mark a run as ``failed`` with an optional JSON error summary."""
    result = await db.execute(select(LoadRun).where(LoadRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        return
    run.status = RunStatus.failed
    run.completed_at = datetime.now(timezone.utc)
    if error_summary:
        run.error_summary = json.dumps(error_summary)
    await db.commit()


# ── CSV row-count utility ─────────────────────────────────────────────────────


def _count_csv_rows(csv_bytes: bytes) -> int:
    """Return the number of *data* rows in a UTF-8 CSV (header row excluded).

    Handles quoted fields that contain embedded newlines correctly via the
    standard :mod:`csv` module.  Returns 0 for empty or header-only content.
    """
    if not csv_bytes or not csv_bytes.strip():
        return 0
    text = csv_bytes.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        next(reader)  # skip header
    except StopIteration:
        return 0
    return sum(1 for _ in reader)
