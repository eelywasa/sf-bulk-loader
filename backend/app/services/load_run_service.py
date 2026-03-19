"""Load Run domain services — abort, summary, logs ZIP, and retry step preparation."""

import io
import logging
import os
import pathlib
import zipfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.job import JobRecord, JobStatus
from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep
from app.schemas.load_run import RunSummaryResponse, RunSummaryStepStats
from app.services.csv_processor import build_retry_partitions
from app.services.salesforce_auth import get_access_token
from app.services.salesforce_bulk import BulkAPIError, SalesforceBulkClient

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {RunStatus.completed, RunStatus.completed_with_errors, RunStatus.failed, RunStatus.aborted}


async def abort_run(db: AsyncSession, run_id: str) -> LoadRun:
    """Set run status to aborted and cascade to in-flight jobs. Raises 404/409."""
    run = await db.get(LoadRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    if run.status not in (RunStatus.pending, RunStatus.running):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot abort a run with status '{run.status.value}'",
        )

    run.status = RunStatus.aborted
    run.completed_at = datetime.now(timezone.utc)

    await db.execute(
        update(JobRecord)
        .where(
            JobRecord.load_run_id == run_id,
            JobRecord.status.in_([JobStatus.pending, JobStatus.uploading, JobStatus.in_progress]),
        )
        .values(status=JobStatus.aborted)
    )

    await db.commit()
    await db.refresh(run)
    return run


async def get_run_summary(db: AsyncSession, run_id: str) -> RunSummaryResponse:
    """Return aggregated success/error counts grouped by load step. Raises 404."""
    run = await db.get(LoadRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    rows = (
        await db.execute(
            select(
                LoadStep.id.label("step_id"),
                LoadStep.object_name,
                LoadStep.sequence,
                func.count(JobRecord.id).label("job_count"),
                func.coalesce(func.sum(JobRecord.records_processed), 0).label("total_processed"),
                func.coalesce(func.sum(JobRecord.records_failed), 0).label("total_failed"),
            )
            .join(JobRecord, JobRecord.load_step_id == LoadStep.id)
            .where(JobRecord.load_run_id == run_id)
            .group_by(LoadStep.id, LoadStep.object_name, LoadStep.sequence)
            .order_by(LoadStep.sequence)
        )
    ).all()

    step_stats: list[RunSummaryStepStats] = []
    grand_records = 0
    grand_success = 0
    grand_errors = 0

    for row in rows:
        total_processed = int(row.total_processed)
        total_failed = int(row.total_failed)
        total_success = total_processed - total_failed
        step_stats.append(
            RunSummaryStepStats(
                step_id=row.step_id,
                object_name=row.object_name,
                sequence=row.sequence,
                total_records=total_processed,
                total_success=total_success,
                total_errors=total_failed,
                job_count=row.job_count,
            )
        )
        grand_records += total_processed
        grand_success += total_success
        grand_errors += total_failed

    return RunSummaryResponse(
        run_id=run_id,
        status=run.status,
        total_records=grand_records,
        total_success=grand_success,
        total_errors=grand_errors,
        steps=step_stats,
    )


async def build_logs_zip(
    db: AsyncSession,
    run_id: str,
    *,
    success: bool,
    errors: bool,
    unprocessed: bool,
) -> io.BytesIO:
    """Build an in-memory ZIP of result CSVs for all jobs in the run. Raises 404."""
    run = await db.get(LoadRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    result = await db.execute(
        select(JobRecord).where(JobRecord.load_run_id == run_id)
    )
    jobs = list(result.scalars().all())

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for job in jobs:
            candidates: list[Optional[str]] = []
            if success:
                candidates.append(job.success_file_path)
            if errors:
                candidates.append(job.error_file_path)
            if unprocessed:
                candidates.append(job.unprocessed_file_path)

            for rel_path in candidates:
                if not rel_path:
                    continue
                full_path = os.path.join(settings.output_dir, rel_path)
                if not os.path.isfile(full_path):
                    continue
                parts = pathlib.PurePosixPath(rel_path.replace("\\", "/")).parts
                archive_name = str(pathlib.PurePosixPath(*parts[1:])) if len(parts) > 1 else rel_path
                zf.write(full_path, archive_name)

    buf.seek(0)
    return buf


async def prepare_retry_step(
    db: AsyncSession,
    run_id: str,
    step_id: str,
    initiated_by: str,
) -> tuple[LoadRun, list]:
    """Validate, build partitions, and create a new LoadRun for a step retry.

    Does NOT enqueue the background task — that stays in the router.
    Raises 404 / 409 / 422 as appropriate.
    """
    original_run = await db.get(LoadRun, run_id)
    if original_run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    if original_run.status not in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot retry a run with status '{original_run.status.value}' — run must be in a terminal state.",
        )

    jobs_result = await db.execute(
        select(JobRecord).where(
            JobRecord.load_run_id == run_id,
            JobRecord.load_step_id == step_id,
        )
    )
    all_step_jobs = list(jobs_result.scalars().all())
    retryable_jobs = [
        j for j in all_step_jobs
        if j.status in (JobStatus.failed, JobStatus.aborted)
        or j.error_file_path is not None
        or j.unprocessed_file_path is not None
    ]
    if not retryable_jobs:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No retryable jobs found for this step.",
        )

    step = await db.get(LoadStep, step_id)
    if step is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")

    # Best-effort abort of any uploading SF jobs that may still be open
    uploading_jobs = [j for j in retryable_jobs if j.sf_job_id and j.status == JobStatus.aborted]
    if uploading_jobs:
        plan_result = await db.execute(
            select(LoadPlan)
            .where(LoadPlan.id == original_run.load_plan_id)
            .options(selectinload(LoadPlan.connection))
        )
        plan = plan_result.scalar_one_or_none()
        if plan is not None:
            try:
                access_token = await get_access_token(db, plan.connection)
                async with SalesforceBulkClient(plan.connection.instance_url, access_token) as bulk_client:
                    for job in uploading_jobs:
                        try:
                            await bulk_client.abort_job(job.sf_job_id)
                        except BulkAPIError as exc:
                            logger.warning(
                                "prepare_retry_step: could not abort SF job %s: %s",
                                job.sf_job_id,
                                exc,
                            )
            except Exception as exc:
                logger.warning("prepare_retry_step: could not obtain token for SF job cleanup: %s", exc)

    partitions = build_retry_partitions(
        job_records=retryable_jobs,
        step=step,
        partition_size=step.partition_size,
        output_dir=settings.output_dir,
    )

    if not partitions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No retryable records found in the result files of the failed jobs.",
        )

    new_run = LoadRun(
        load_plan_id=original_run.load_plan_id,
        status=RunStatus.pending,
        initiated_by=initiated_by,
        retry_of_run_id=run_id,
    )
    db.add(new_run)
    await db.commit()
    await db.refresh(new_run)

    logger.info(
        "Retry run %s created for original run %s step %s (initiated_by=%s)",
        new_run.id,
        run_id,
        step_id,
        initiated_by,
    )
    return new_run, partitions
