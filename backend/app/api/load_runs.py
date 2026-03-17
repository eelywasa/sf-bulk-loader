"""Load Runs API — list, inspect, abort, and summarise load run executions."""

import io
import logging
import os
import pathlib
import zipfile
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings

from app.database import get_db
from app.services.auth import get_current_user
from app.models.job import JobRecord, JobStatus
from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep
from app.models.user import User
from app.schemas.load_run import (
    LoadRunDetailResponse,
    LoadRunResponse,
    RunSummaryResponse,
    RunSummaryStepStats,
)
from app.services import orchestrator
from app.services.csv_processor import build_retry_partitions
from app.services.salesforce_bulk import BulkAPIError, SalesforceBulkClient
from app.services.salesforce_auth import get_access_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["load-runs"], dependencies=[Depends(get_current_user)])


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _get_run_or_404(run_id: str, db: AsyncSession) -> LoadRun:
    run = await db.get(LoadRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get("/", response_model=List[LoadRunResponse])
async def list_runs(
    plan_id: Optional[str] = None,
    run_status: Optional[RunStatus] = None,
    started_after: Optional[datetime] = None,
    started_before: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
) -> List[LoadRun]:
    query = select(LoadRun)
    if plan_id is not None:
        query = query.where(LoadRun.load_plan_id == plan_id)
    if run_status is not None:
        query = query.where(LoadRun.status == run_status)
    if started_after is not None:
        query = query.where(LoadRun.started_at >= started_after)
    if started_before is not None:
        query = query.where(LoadRun.started_at <= started_before)
    query = query.order_by(LoadRun.started_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{run_id}", response_model=LoadRunDetailResponse)
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)) -> LoadRun:
    result = await db.execute(
        select(LoadRun)
        .where(LoadRun.id == run_id)
        .options(selectinload(LoadRun.job_records))
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


@router.post("/{run_id}/abort", response_model=LoadRunResponse)
async def abort_run(run_id: str, db: AsyncSession = Depends(get_db)) -> LoadRun:
    """Abort a pending or running load. In-progress jobs are marked aborted."""
    run = await _get_run_or_404(run_id, db)

    if run.status not in (RunStatus.pending, RunStatus.running):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot abort a run with status '{run.status.value}'",
        )

    run.status = RunStatus.aborted
    run.completed_at = datetime.now(timezone.utc)

    # Mark any in-flight jobs so the orchestrator knows not to wait on them
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


@router.get("/{run_id}/summary", response_model=RunSummaryResponse)
async def get_run_summary(run_id: str, db: AsyncSession = Depends(get_db)) -> RunSummaryResponse:
    """Return aggregated success/error counts grouped by load step."""
    run = await _get_run_or_404(run_id, db)

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

    step_stats: List[RunSummaryStepStats] = []
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


@router.get("/{run_id}/logs.zip")
async def download_logs_zip(
    run_id: str,
    success: bool = True,
    errors: bool = True,
    unprocessed: bool = True,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream a ZIP of all selected result CSVs for every job in this run.

    Files are organised as ``{step_id}/partition_{n}_{type}.csv`` inside the archive.
    Only file types whose query parameter is ``true`` are included.
    Files that were not generated (e.g. no errors) are silently skipped.
    """
    await _get_run_or_404(run_id, db)

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
                # Strip the leading run_id segment so archive paths are
                # {step_id}/partition_{n}_{type}.csv
                parts = pathlib.PurePosixPath(rel_path.replace("\\", "/")).parts
                archive_name = str(pathlib.PurePosixPath(*parts[1:])) if len(parts) > 1 else rel_path
                zf.write(full_path, archive_name)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="run_{run_id[:8]}_logs.zip"',
        },
    )


_TERMINAL_STATUSES = {RunStatus.completed, RunStatus.completed_with_errors, RunStatus.failed, RunStatus.aborted}


@router.post(
    "/{run_id}/retry-step/{step_id}",
    response_model=LoadRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def retry_step(
    run_id: str,
    step_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LoadRun:
    """Create a new LoadRun that retries only the failed/aborted jobs of one step.

    Sources its CSV data from the error and unprocessed result files of the
    original failed jobs rather than re-globbing the original CSV files.
    """
    # 1. Load original run; 404 if not found
    original_run = await _get_run_or_404(run_id, db)

    # 2. 409 if run is not in a terminal state
    if original_run.status not in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot retry a run with status '{original_run.status.value}' — run must be in a terminal state.",
        )

    # 3. Load jobs that have retryable data for this step; 422 if none.
    # This covers:
    #   - failed/aborted jobs (Track B: re-submit original partition)
    #   - job_complete jobs with error/unprocessed result files (Track A: retry failed records)
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

    # Load the step for partition metadata
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
                                "retry_step: could not abort SF job %s: %s",
                                job.sf_job_id,
                                exc,
                            )
            except Exception as exc:
                logger.warning("retry_step: could not obtain token for SF job cleanup: %s", exc)

    # 4. Build partitions from error/unprocessed files
    partitions = build_retry_partitions(
        job_records=retryable_jobs,
        step=step,
        partition_size=step.partition_size,
        output_dir=settings.output_dir,
    )

    # 5. 422 if no retryable records found
    if not partitions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No retryable records found in the result files of the failed jobs.",
        )

    # 6. Create new LoadRun
    new_run = LoadRun(
        load_plan_id=original_run.load_plan_id,
        status=RunStatus.pending,
        initiated_by=current_user.username,
        retry_of_run_id=run_id,
    )
    db.add(new_run)
    await db.commit()
    await db.refresh(new_run)

    # 7. Enqueue background execution
    background_tasks.add_task(orchestrator.execute_retry_run, new_run.id, step_id, partitions)

    logger.info(
        "Retry run %s created for original run %s step %s (initiated_by=%s)",
        new_run.id,
        run_id,
        step_id,
        current_user.username,
    )
    return new_run
