"""Jobs API — inspect individual Bulk API jobs and download result CSVs."""

import csv
import json
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.job import JobRecord, JobStatus
from app.schemas.job import JobResponse
from app.services.auth import get_current_user
from app.services.input_storage import InputStorageError, _validate_filters, _row_matches

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jobs"], dependencies=[Depends(get_current_user)])


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _get_job_or_404(job_id: str, db: AsyncSession) -> JobRecord:
    job = await db.get(JobRecord, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def _parse_filters_param(filters: Optional[str]) -> list[dict[str, str]] | None:
    """Parse and structurally validate the filters query parameter JSON string.

    Raises HTTPException(400) on malformed JSON, non-array JSON, or items
    missing 'column'/'value' keys. Column-level validation (unknown columns,
    duplicates) is deferred to _validate_filters inside _preview_csv.
    """
    if filters is None:
        return None
    try:
        parsed = json.loads(filters)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid filters JSON: {exc}",
        ) from exc
    if not isinstance(parsed, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="filters must be a JSON array",
        )
    for item in parsed:
        if not isinstance(item, dict) or "column" not in item or "value" not in item:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Each filter must be an object with 'column' and 'value' keys",
            )
    return parsed


def _preview_csv(
    relative_path: Optional[str],
    description: str,
    limit: int,
    offset: int = 0,
    filters: list[dict[str, str]] | None = None,
) -> Dict[str, Any]:
    """Return a paginated page of data rows from a result CSV."""
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
    try:
        with open(full_path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or [])

            if not filters:
                # Unfiltered path: skip offset rows, read limit+1 for has_next sentinel
                for _ in zip(range(offset), reader):
                    pass
                buffer = [dict(row) for _, row in zip(range(limit + 1), reader)]
                has_next = len(buffer) > limit
                page_rows = buffer[:limit]
                total_rows = None
                filtered_rows = None
            else:
                # Filtered path: full scan
                try:
                    filter_tuples = _validate_filters(header, filters)
                except InputStorageError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=str(exc),
                    ) from exc
                page_rows: list[dict] = []
                match_count = 0
                total_scanned = 0
                for row in reader:
                    total_scanned += 1
                    if _row_matches(row, filter_tuples):
                        if match_count >= offset and len(page_rows) < limit:
                            page_rows.append(dict(row))
                        match_count += 1
                has_next = match_count > offset + limit
                total_rows = total_scanned
                filtered_rows = match_count
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not read file: {exc}",
        ) from exc
    return {
        "filename": os.path.basename(full_path),
        "header": header,
        "rows": page_rows,
        "total_rows": total_rows,
        "filtered_rows": filtered_rows,
        "offset": offset,
        "limit": limit,
        "has_next": has_next,
    }


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


@router.get("/api/jobs/{job_id}/success-csv/preview")
async def preview_success_csv(
    job_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    filters: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    job = await _get_job_or_404(job_id, db)
    parsed_filters = _parse_filters_param(filters)
    return await run_in_threadpool(
        _preview_csv, job.success_file_path, "Success CSV", limit, offset, parsed_filters
    )


@router.get("/api/jobs/{job_id}/error-csv/preview")
async def preview_error_csv(
    job_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    filters: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    job = await _get_job_or_404(job_id, db)
    parsed_filters = _parse_filters_param(filters)
    return await run_in_threadpool(
        _preview_csv, job.error_file_path, "Error CSV", limit, offset, parsed_filters
    )


@router.get("/api/jobs/{job_id}/unprocessed-csv/preview")
async def preview_unprocessed_csv(
    job_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    filters: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    job = await _get_job_or_404(job_id, db)
    parsed_filters = _parse_filters_param(filters)
    return await run_in_threadpool(
        _preview_csv, job.unprocessed_file_path, "Unprocessed records CSV", limit, offset, parsed_filters
    )
