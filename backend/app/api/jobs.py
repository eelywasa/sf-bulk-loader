"""Jobs API — inspect individual Bulk API jobs and download result CSVs."""

import csv
import io
import json
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import FILES_VIEW_CONTENTS, RUNS_VIEW, require_permission
from app.config import settings
from app.database import get_db
from app.models.job import JobRecord, JobStatus
from app.models.load_run import LoadRun
from app.models.load_plan import LoadPlan
from app.schemas.job import JobResponse
from app.services.auth import get_current_user
from app.services.input_storage import InputStorageError, _validate_filters, _row_matches
from app.services.output_storage import OutputStorage, OutputStorageError, get_output_storage

logger = logging.getLogger(__name__)

_require_view = require_permission(RUNS_VIEW)
_require_file_contents = require_permission(FILES_VIEW_CONTENTS)

router = APIRouter(tags=["jobs"], dependencies=[Depends(_require_view)])


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


def _parse_csv_stream(
    fh: Any,
    filename: str,
    limit: int,
    offset: int,
    filters: list[dict[str, str]] | None,
) -> Dict[str, Any]:
    reader = csv.DictReader(fh)
    header = list(reader.fieldnames or [])
    if not filters:
        for _ in zip(range(offset), reader):
            pass
        buffer = [dict(row) for _, row in zip(range(limit + 1), reader)]
        has_next = len(buffer) > limit
        page_rows = buffer[:limit]
        total_rows = None
        filtered_rows = None
    else:
        try:
            filter_tuples = _validate_filters(header, filters)
        except InputStorageError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
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
    return {
        "filename": filename,
        "header": header,
        "rows": page_rows,
        "total_rows": total_rows,
        "filtered_rows": filtered_rows,
        "offset": offset,
        "limit": limit,
        "has_next": has_next,
    }


def _preview_csv(
    relative_path: Optional[str],
    description: str,
    limit: int,
    offset: int = 0,
    filters: list[dict[str, str]] | None = None,
) -> Dict[str, Any]:
    """Return a paginated page of data rows from a local result CSV."""
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
            return _parse_csv_stream(fh, os.path.basename(full_path), limit, offset, filters)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not read file: {exc}",
        ) from exc


def _preview_csv_from_bytes(
    data: bytes,
    filename: str,
    limit: int,
    offset: int,
    filters: list[dict[str, str]] | None,
) -> Dict[str, Any]:
    fh = io.StringIO(data.decode("utf-8-sig"))
    return _parse_csv_stream(fh, filename, limit, offset, filters)


def _serve_csv(relative_path: Optional[str], description: str) -> FileResponse:
    """Return a FileResponse for a local result CSV or raise 404 if unavailable."""
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


async def _get_output_storage_for_job(job: JobRecord, db: AsyncSession) -> OutputStorage:
    result = await db.execute(
        select(LoadRun)
        .options(joinedload(LoadRun.load_plan))
        .where(LoadRun.id == job.load_run_id)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found for job")
    return await get_output_storage(run.load_plan.output_connection_id, db)


async def _serve_result_file(
    job: JobRecord,
    ref: Optional[str],
    description: str,
    db: AsyncSession,
) -> Any:
    if not ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{description} is not available for this job",
        )
    if ref.startswith("s3://"):
        storage = await _get_output_storage_for_job(job, db)
        try:
            data = await run_in_threadpool(storage.read_bytes, ref)
        except OutputStorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not download {description} from S3: {exc}",
            ) from exc
        filename = ref.rsplit("/", 1)[-1]
        return Response(
            content=data,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return _serve_csv(ref, description)


async def _preview_result_file(
    job: JobRecord,
    ref: Optional[str],
    description: str,
    limit: int,
    offset: int,
    filters: list[dict[str, str]] | None,
    db: AsyncSession,
) -> Dict[str, Any]:
    if not ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{description} is not available for this job",
        )
    if ref.startswith("s3://"):
        storage = await _get_output_storage_for_job(job, db)
        try:
            data = await run_in_threadpool(storage.read_bytes, ref)
        except OutputStorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not download {description} from S3: {exc}",
            ) from exc
        filename = ref.rsplit("/", 1)[-1]
        return await run_in_threadpool(_preview_csv_from_bytes, data, filename, limit, offset, filters)
    return await run_in_threadpool(_preview_csv, ref, description, limit, offset, filters)


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
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)) -> JobResponse:
    result = await db.execute(
        select(JobRecord)
        .options(
            joinedload(JobRecord.load_run)
            .joinedload(LoadRun.load_plan)
            .joinedload(LoadPlan.connection)
        )
        .where(JobRecord.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    response = JobResponse.model_validate(job)
    try:
        instance_url = job.load_run.load_plan.connection.instance_url
        response = response.model_copy(update={"sf_instance_url": instance_url})
    except AttributeError:
        pass
    return response


@router.get("/api/jobs/{job_id}/success-csv", dependencies=[Depends(_require_file_contents)])
async def download_success_csv(job_id: str, db: AsyncSession = Depends(get_db)) -> Any:
    job = await _get_job_or_404(job_id, db)
    return await _serve_result_file(job, job.success_file_path, "Success CSV", db)


@router.get("/api/jobs/{job_id}/error-csv", dependencies=[Depends(_require_file_contents)])
async def download_error_csv(job_id: str, db: AsyncSession = Depends(get_db)) -> Any:
    job = await _get_job_or_404(job_id, db)
    return await _serve_result_file(job, job.error_file_path, "Error CSV", db)


@router.get("/api/jobs/{job_id}/unprocessed-csv", dependencies=[Depends(_require_file_contents)])
async def download_unprocessed_csv(job_id: str, db: AsyncSession = Depends(get_db)) -> Any:
    job = await _get_job_or_404(job_id, db)
    return await _serve_result_file(job, job.unprocessed_file_path, "Unprocessed records CSV", db)


@router.get("/api/jobs/{job_id}/success-csv/preview", dependencies=[Depends(_require_file_contents)])
async def preview_success_csv(
    job_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    filters: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    job = await _get_job_or_404(job_id, db)
    parsed_filters = _parse_filters_param(filters)
    return await _preview_result_file(job, job.success_file_path, "Success CSV", limit, offset, parsed_filters, db)


@router.get("/api/jobs/{job_id}/error-csv/preview", dependencies=[Depends(_require_file_contents)])
async def preview_error_csv(
    job_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    filters: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    job = await _get_job_or_404(job_id, db)
    parsed_filters = _parse_filters_param(filters)
    return await _preview_result_file(job, job.error_file_path, "Error CSV", limit, offset, parsed_filters, db)


@router.get("/api/jobs/{job_id}/unprocessed-csv/preview", dependencies=[Depends(_require_file_contents)])
async def preview_unprocessed_csv(
    job_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    filters: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    job = await _get_job_or_404(job_id, db)
    parsed_filters = _parse_filters_param(filters)
    return await _preview_result_file(job, job.unprocessed_file_path, "Unprocessed records CSV", limit, offset, parsed_filters, db)
