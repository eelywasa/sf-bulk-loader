"""Load Run domain services — abort, logs ZIP, and retry step preparation."""

import io
import logging
import pathlib
import zipfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.job import JobRecord, JobStatus
from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep
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

    # Read each result file via the storage matching its PERSISTED ref shape,
    # not the run's current resolved backend (SFBL-385). A run can hold
    # mixed-vintage refs — e.g. an aws_hosted run created before this upgrade
    # (local relative paths) whose plan now resolves to S3, or vice-versa.
    # Routing every ref through one backend would mis-read the others; instead
    # local relative refs go to the local output dir and s3:// refs go to the
    # run's S3 storage. read_bytes handles each backend's read.
    from app.services.output_storage import (  # noqa: PLC0415
        LocalOutputStorage,
        OutputStorageError,
        get_output_storage,
    )
    from app.services.settings.dirs import effective_output_dir  # noqa: PLC0415

    local_storage = LocalOutputStorage(await effective_output_dir())
    plan = await db.get(LoadPlan, run.load_plan_id)
    s3_storage = await get_output_storage(plan.output_connection_id if plan else None, db)

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

            for ref in candidates:
                if not ref:
                    continue
                reader = s3_storage if ref.startswith("s3://") else local_storage
                try:
                    data = reader.read_bytes(ref)
                except (OutputStorageError, FileNotFoundError):
                    # Missing/inaccessible result file — skip it (matches the
                    # prior os.path.isfile skip behaviour).
                    continue
                zf.writestr(_zip_member_name(ref, reader), data)

    buf.seek(0)
    return buf


def _zip_member_name(ref: str, storage: object) -> str:
    """Return the in-archive name for a result-file *ref*.

    Drops the leading path component (the plan/run grouping dir) so the archive
    is rooted at the run, matching the historical local layout. Works for both
    local relative paths and ``s3://bucket/<prefix><rel>`` URIs — for S3 the
    bucket and configured root prefix are stripped first so the member name
    matches the local layout exactly.
    """
    rel = ref
    if ref.startswith("s3://"):
        bucket = getattr(storage, "_bucket", "") or ""
        root_prefix = getattr(storage, "_root_prefix", "") or ""
        scheme_bucket = f"s3://{bucket}/"
        key = ref[len(scheme_bucket):] if ref.startswith(scheme_bucket) else ref[len("s3://"):]
        rel = key[len(root_prefix):] if root_prefix and key.startswith(root_prefix) else key
    parts = pathlib.PurePosixPath(rel.replace("\\", "/")).parts
    return str(pathlib.PurePosixPath(*parts[1:])) if len(parts) > 1 else rel


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

    step = await db.get(LoadStep, step_id)
    if step is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")

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

    from app.services.settings.dirs import effective_output_dir  # noqa: PLC0415
    from app.services.output_storage import get_output_storage  # noqa: PLC0415

    # The output storage the original run wrote its result files to — local on
    # the filesystem profiles, the first-party S3 bucket on aws_hosted with no
    # explicit output connection (SFBL-385). Track A reads route through it so
    # s3:// result refs are read from S3 instead of the local output dir.
    retry_plan = await db.get(LoadPlan, original_run.load_plan_id)
    output_storage = await get_output_storage(
        retry_plan.output_connection_id if retry_plan else None, db
    )

    partitions = await build_retry_partitions(
        job_records=retryable_jobs,
        step=step,
        partition_size=step.partition_size,
        output_dir=await effective_output_dir(),
        db=db,
        output_storage=output_storage,
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
