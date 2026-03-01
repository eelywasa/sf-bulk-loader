"""Load Runs API — list, inspect, abort, and summarise load run executions."""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.job import JobRecord, JobStatus
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep
from app.schemas.load_run import (
    LoadRunDetailResponse,
    LoadRunResponse,
    RunSummaryResponse,
    RunSummaryStepStats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["load-runs"])


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
