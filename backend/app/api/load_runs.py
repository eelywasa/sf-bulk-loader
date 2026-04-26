"""Load Runs API — list, inspect, abort, and summarise load run executions."""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.permissions import (
    FILES_VIEW_CONTENTS,
    RUNS_ABORT,
    RUNS_EXECUTE,
    RUNS_VIEW,
    require_permission,
)
from app.database import get_db
from app.services.auth import get_current_user
from app.models.load_run import LoadRun, RunStatus
from app.models.user import User  # noqa: F401 (used in type hints for dependency params)
from app.schemas.load_run import (
    LoadRunDetailResponse,
    LoadRunResponse,
)
from app.services import orchestrator
from app.services import load_run_service

logger = logging.getLogger(__name__)

_require_view = require_permission(RUNS_VIEW)
_require_execute = require_permission(RUNS_EXECUTE)
_require_abort = require_permission(RUNS_ABORT)
_require_file_contents = require_permission(FILES_VIEW_CONTENTS)

router = APIRouter(prefix="/api/runs", tags=["load-runs"], dependencies=[Depends(_require_view)])


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
    query = query.order_by(LoadRun.started_at.desc().nulls_last())
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
async def abort_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _abort: User = Depends(_require_abort),
) -> LoadRun:
    """Abort a pending or running load. In-progress jobs are marked aborted."""
    return await load_run_service.abort_run(db, run_id)


@router.get("/{run_id}/logs.zip", dependencies=[Depends(_require_file_contents)])
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
    buf = await load_run_service.build_logs_zip(
        db, run_id, success=success, errors=errors, unprocessed=unprocessed
    )
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="run_{run_id[:8]}_logs.zip"',
        },
    )


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
    current_user: User = Depends(_require_execute),
) -> LoadRun:
    """Create a new LoadRun that retries only the failed/aborted jobs of one step."""
    new_run, partitions = await load_run_service.prepare_retry_step(
        db, run_id, step_id, current_user.email
    )
    background_tasks.add_task(orchestrator.execute_retry_run, new_run.id, step_id, partitions)
    return new_run
