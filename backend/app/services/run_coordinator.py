"""Run lifecycle coordinator: entry points, run-level state transitions.

Owns the top-level orchestration flow for both normal runs and retry runs.
Collaborates with :mod:`step_executor` for per-step work and
:mod:`run_event_publisher` for WebSocket events.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models.job import JobRecord, JobStatus
from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep
from app.services import step_executor as _step_executor_mod
from app.services.csv_processor import discover_files as _default_discover, partition_csv as _default_partition
from app.services.partition_executor import process_partition as _default_process
from app.services.result_persistence import count_csv_rows
from app.services.run_event_publisher import (
    publish_run_aborted,
    publish_run_completed,
    publish_run_failed,
    publish_run_started,
    publish_step_completed,
    publish_step_started,
)
from app.services.salesforce_auth import get_access_token as _default_get_token
from app.services.salesforce_bulk import BulkAPIError, SalesforceBulkClient

logger = logging.getLogger(__name__)

_DbFactory = Callable[[], AsyncSession]


async def execute_run(run_id: str) -> None:
    """Background entry point: run a LoadRun end-to-end."""
    async with AsyncSessionLocal() as db:
        await _execute_run(run_id, db, db_factory=AsyncSessionLocal)


async def execute_retry_run(
    run_id: str,
    step_id: str,
    partitions: list[bytes],
    *,
    _get_token: Callable = _default_get_token,
    _BulkClient: type = SalesforceBulkClient,
) -> None:
    """Background entry point: execute a retry run for a single step.

    Processes the supplied pre-built CSV *partitions* rather than re-globbing
    the original CSV files.
    """
    async with AsyncSessionLocal() as db:
        # ── Load run + plan + connection ──────────────────────────────────────
        result = await db.execute(
            select(LoadRun)
            .where(LoadRun.id == run_id)
            .options(
                selectinload(LoadRun.load_plan).selectinload(LoadPlan.connection),
            )
        )
        run = result.scalar_one_or_none()
        if run is None:
            logger.error("execute_retry_run: LoadRun %s not found", run_id)
            return

        step_result = await db.execute(select(LoadStep).where(LoadStep.id == step_id))
        step = step_result.scalar_one_or_none()
        if step is None:
            logger.error("execute_retry_run: LoadStep %s not found", step_id)
            await _mark_run_failed(run_id, db)
            return

        plan: LoadPlan = run.load_plan

        # ── Mark run as running ───────────────────────────────────────────────
        run.status = RunStatus.running
        run.started_at = datetime.now(timezone.utc)
        await db.commit()

        await publish_run_started(run_id)
        logger.info(
            "Retry run %s started: step=%s partitions=%d",
            run_id,
            step_id,
            len(partitions),
        )

        # ── Obtain Salesforce access token ────────────────────────────────────
        try:
            access_token = await _get_token(db, plan.connection)
        except Exception as exc:
            logger.error("Retry run %s: failed to obtain access token: %s", run_id, exc)
            await _mark_run_failed(run_id, db, error_summary={"auth_error": str(exc)})
            await publish_run_failed(run_id, error=str(exc))
            return

        semaphore = asyncio.Semaphore(plan.max_parallel_jobs)

        # ── Create JobRecord rows ─────────────────────────────────────────────
        job_record_ids = await _build_retry_job_records(db, run_id, step_id, partitions)

        # ── Process partitions concurrently ───────────────────────────────────
        async with _BulkClient(plan.connection.instance_url, access_token) as bulk_client:
            tasks = [
                _default_process(
                    run_id=run_id,
                    step=step,
                    job_record_id=jr_id,
                    csv_data=csv_data,
                    bulk_client=bulk_client,
                    semaphore=semaphore,
                    db_factory=AsyncSessionLocal,
                )
                for jr_id, csv_data in zip(job_record_ids, partitions)
            ]
            gather_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Transition any stuck intermediate-state jobs to failed
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

        # ── Aggregate results ─────────────────────────────────────────────────
        total_success = 0
        total_errors = 0
        for i, res in enumerate(gather_results):
            if isinstance(res, Exception):
                logger.error(
                    "Retry run %s partition %d: unhandled exception: %s",
                    run_id,
                    i,
                    res,
                )
            elif isinstance(res, tuple):
                success, errors = res
                total_success += success
                total_errors += errors

        total_records = total_success + total_errors
        final_status = (
            RunStatus.completed_with_errors if total_errors > 0 else RunStatus.completed
        )

        run.status = final_status
        run.completed_at = datetime.now(timezone.utc)
        run.total_records = total_records
        run.total_success = total_success
        run.total_errors = total_errors
        await db.commit()

        await publish_run_completed(
            run_id,
            status=final_status.value,
            total_records=total_records,
            total_success=total_success,
            total_errors=total_errors,
        )
        logger.info(
            "Retry run %s completed (%s): records=%d success=%d errors=%d",
            run_id,
            final_status.value,
            total_records,
            total_success,
            total_errors,
        )


async def _build_retry_job_records(
    db: AsyncSession,
    run_id: str,
    step_id: str,
    partitions: list[bytes],
) -> list[str]:
    """Create ``JobRecord`` rows for retry partitions.

    Isolates retry-partition construction from retry-run submission.

    Returns:
        List of ``JobRecord`` IDs in partition order.
    """
    job_records: list[JobRecord] = []
    for idx, csv_data in enumerate(partitions):
        jr = JobRecord(
            load_run_id=run_id,
            load_step_id=step_id,
            partition_index=idx,
            status=JobStatus.pending,
            total_records=count_csv_rows(csv_data),
        )
        db.add(jr)
        job_records.append(jr)
    await db.commit()
    for jr in job_records:
        await db.refresh(jr)
    return [jr.id for jr in job_records]


async def _execute_run(
    run_id: str,
    db: AsyncSession,
    *,
    db_factory: _DbFactory = AsyncSessionLocal,
    _get_token: Callable = _default_get_token,
    _BulkClient: type = SalesforceBulkClient,
    _discover: Callable = _default_discover,
    _partition: Callable = _default_partition,
) -> None:
    """Orchestrate a load run.

    Injectable parameters allow the orchestrator facade to pass patched
    bindings so that existing ``patch("app.services.orchestrator.X")`` calls
    in tests continue to work.
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

    await publish_run_started(run_id)
    logger.info(
        "Run %s started: plan=%s steps=%d max_parallel_jobs=%d",
        run_id,
        plan.id,
        len(steps),
        plan.max_parallel_jobs,
    )

    # ── Obtain Salesforce access token ────────────────────────────────────────
    try:
        access_token = await _get_token(db, plan.connection)
    except Exception as exc:
        logger.error("Run %s: failed to obtain access token: %s", run_id, exc)
        await _mark_run_failed(run_id, db, error_summary={"auth_error": str(exc)})
        await publish_run_failed(run_id, error=str(exc))
        return

    semaphore = asyncio.Semaphore(plan.max_parallel_jobs)
    run_total_records = 0
    run_total_success = 0
    run_total_errors = 0
    any_step_failed = False

    # ── Execute steps in sequence ─────────────────────────────────────────────
    async with _BulkClient(plan.connection.instance_url, access_token) as bulk_client:
        for step in steps:
            # Reload run to detect external abort before starting next step.
            await db.refresh(run)
            if run.status == RunStatus.aborted:
                logger.info("Run %s: aborted before step %s", run_id, step.id)
                await publish_run_aborted(run_id)
                return

            await publish_step_started(
                run_id,
                step_id=step.id,
                object_name=step.object_name,
                sequence=step.sequence,
            )
            logger.info(
                "Run %s: starting step %d — %s %s",
                run_id,
                step.sequence,
                step.operation.value,
                step.object_name,
            )

            step_success, step_errors = await _step_executor_mod.execute_step(
                run_id=run_id,
                step=step,
                bulk_client=bulk_client,
                db=db,
                semaphore=semaphore,
                db_factory=db_factory,
                _discover=_discover,
                _partition=_partition,
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

            await publish_step_completed(
                run_id,
                step_id=step.id,
                object_name=step.object_name,
                records_processed=total_step_records,
                records_success=step_success,
                records_failed=step_errors,
                step_failed=step_failed,
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
                    await publish_run_aborted(run_id, reason="step_failure_threshold")
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

    await publish_run_completed(
        run_id,
        status=final_status.value,
        total_records=run_total_records,
        total_success=run_total_success,
        total_errors=run_total_errors,
    )
    logger.info(
        "Run %s completed (%s): records=%d success=%d errors=%d",
        run_id,
        final_status.value,
        run_total_records,
        run_total_success,
        run_total_errors,
    )


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
