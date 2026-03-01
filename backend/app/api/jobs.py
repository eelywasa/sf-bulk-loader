"""Jobs API — inspect individual Bulk API jobs and download result CSVs."""

import logging
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.job import JobRecord, JobStatus
from app.schemas.job import JobResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jobs"])


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _get_job_or_404(job_id: str, db: AsyncSession) -> JobRecord:
    job = await db.get(JobRecord, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def _serve_csv(relative_path: Optional[str], description: str) -> FileResponse:
    """Return a FileResponse for a result CSV or raise 404 if unavailable."""
    if not relative_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{description} is not available for this job",
        )
    full_path = os.path.join(settings.output_dir, relative_path)
    if not os.path.isfile(full_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{description} file not found on disk",
        )
    return FileResponse(
        path=full_path,
        media_type="text/csv",
        filename=os.path.basename(full_path),
    )


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get("/api/runs/{run_id}/jobs", response_model=List[JobResponse])
async def list_jobs(
    run_id: str,
    step_id: Optional[str] = None,
    job_status: Optional[JobStatus] = None,
    db: AsyncSession = Depends(get_db),
) -> List[JobRecord]:
    query = select(JobRecord).where(JobRecord.load_run_id == run_id)
    if step_id is not None:
        query = query.where(JobRecord.load_step_id == step_id)
    if job_status is not None:
        query = query.where(JobRecord.status == job_status)
    query = query.order_by(JobRecord.partition_index)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/api/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)) -> JobRecord:
    return await _get_job_or_404(job_id, db)


@router.get("/api/jobs/{job_id}/success-csv")
async def download_success_csv(job_id: str, db: AsyncSession = Depends(get_db)) -> FileResponse:
    job = await _get_job_or_404(job_id, db)
    return _serve_csv(job.success_file_path, "Success CSV")


@router.get("/api/jobs/{job_id}/error-csv")
async def download_error_csv(job_id: str, db: AsyncSession = Depends(get_db)) -> FileResponse:
    job = await _get_job_or_404(job_id, db)
    return _serve_csv(job.error_file_path, "Error CSV")


@router.get("/api/jobs/{job_id}/unprocessed-csv")
async def download_unprocessed_csv(job_id: str, db: AsyncSession = Depends(get_db)) -> FileResponse:
    job = await _get_job_or_404(job_id, db)
    return _serve_csv(job.unprocessed_file_path, "Unprocessed records CSV")
