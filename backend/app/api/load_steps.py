"""Load Steps API — manage steps within a load plan."""

import csv
import logging
import pathlib
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.input_connection import InputConnection
from app.models.load_plan import LoadPlan
from app.models.load_step import LoadStep, QUERY_OPERATIONS
from app.services.auth import get_current_user
from app.services.input_storage import (
    InputConnectionNotFoundError,
    InputStorageError,
    UnsupportedInputProviderError,
    get_storage,
)
from app.services import load_step_service
from app.services.salesforce_auth import AuthError, get_access_token
from app.services.salesforce_query_validation import BulkAPIError, explain_soql
from app.schemas.load_step import (
    FilePreviewInfo,
    LoadStepCreate,
    LoadStepResponse,
    LoadStepUpdate,
    StepPreviewResponse,
    StepReorderRequest,
    ValidateSoqlRequest,
    ValidateSoqlResponse,
    _validate_query_dml_fields,
)

logger = logging.getLogger(__name__)

# Steps are nested under /api/load-plans
router = APIRouter(prefix="/api/load-plans", tags=["load-steps"], dependencies=[Depends(get_current_user)])


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _get_plan_or_404(plan_id: str, db: AsyncSession) -> LoadPlan:
    plan = await db.get(LoadPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Load plan not found")
    return plan


async def _get_step_or_404(plan_id: str, step_id: str, db: AsyncSession) -> LoadStep:
    result = await db.execute(
        select(LoadStep).where(LoadStep.id == step_id, LoadStep.load_plan_id == plan_id)
    )
    step = result.scalar_one_or_none()
    if step is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Load step not found")
    return step


async def _validate_input_connection_direction(input_connection_id: str, db: AsyncSession) -> None:
    """Raise 422 if input_connection_id references a connection that cannot be used as input."""
    ic = await db.get(InputConnection, input_connection_id)
    if ic is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Input connection '{input_connection_id}' not found",
        )
    if ic.direction not in ("in", "both"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Storage connection '{input_connection_id}' has direction '{ic.direction}' "
                "but must be 'in' or 'both' to be used as a step input source"
            ),
        )


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.post("/{plan_id}/steps", response_model=LoadStepResponse, status_code=status.HTTP_201_CREATED)
async def add_step(
    plan_id: str,
    data: LoadStepCreate,
    db: AsyncSession = Depends(get_db),
) -> LoadStep:
    await _get_plan_or_404(plan_id, db)

    if data.input_connection_id is not None:
        await _validate_input_connection_direction(data.input_connection_id, db)

    step_data = data.model_dump()
    if step_data.get("sequence") is None:
        step_data["sequence"] = await load_step_service.next_sequence(db, plan_id)
    step = LoadStep(load_plan_id=plan_id, **step_data)
    db.add(step)
    await db.commit()
    await db.refresh(step)
    return step


# NOTE: /reorder must be defined before /{step_id} routes to avoid path conflicts.
@router.post("/{plan_id}/steps/reorder", response_model=List[LoadStepResponse])
async def reorder_steps(
    plan_id: str,
    data: StepReorderRequest,
    db: AsyncSession = Depends(get_db),
) -> List[LoadStep]:
    """Reassign step sequences based on the ordered list of step IDs provided."""
    await _get_plan_or_404(plan_id, db)
    return await load_step_service.reorder_steps(db, plan_id, data.step_ids)


@router.put("/{plan_id}/steps/{step_id}", response_model=LoadStepResponse)
async def update_step(
    plan_id: str,
    step_id: str,
    data: LoadStepUpdate,
    db: AsyncSession = Depends(get_db),
) -> LoadStep:
    step = await _get_step_or_404(plan_id, step_id, db)
    update_data = data.model_dump(exclude_unset=True)

    if "input_connection_id" in update_data and update_data["input_connection_id"] is not None:
        await _validate_input_connection_direction(update_data["input_connection_id"], db)

    # Cross-field validation against the composed final state: merge the
    # existing step's operation/soql/csv_file_pattern with the incoming patch
    # so partial updates cannot produce invalid combinations (e.g. clearing
    # csv_file_pattern on a DML step, or setting soql on one).
    effective_operation = update_data.get("operation", step.operation)
    effective_soql = update_data["soql"] if "soql" in update_data else step.soql
    effective_pattern = (
        update_data["csv_file_pattern"]
        if "csv_file_pattern" in update_data
        else step.csv_file_pattern
    )
    try:
        _validate_query_dml_fields(
            effective_operation,
            effective_soql,
            effective_pattern,
            context="update",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    for field, value in update_data.items():
        setattr(step, field, value)
    await db.commit()
    await db.refresh(step)
    return step


@router.delete("/{plan_id}/steps/{step_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_step(
    plan_id: str,
    step_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    step = await _get_step_or_404(plan_id, step_id, db)
    await db.delete(step)
    await db.commit()


@router.post("/{plan_id}/validate-soql", response_model=ValidateSoqlResponse)
async def validate_soql(
    plan_id: str,
    data: ValidateSoqlRequest,
    db: AsyncSession = Depends(get_db),
) -> ValidateSoqlResponse:
    """Validate an ad-hoc SOQL string against the plan's Salesforce connection.

    Unlike the step preview endpoint, this does not require the SOQL to be
    persisted on a step first — callers pass the SOQL directly so unsaved
    edits in the step editor can be validated immediately.
    """
    soql = (data.soql or "").strip()
    if not soql:
        return ValidateSoqlResponse(valid=False, error="SOQL is empty")

    result = await db.execute(
        select(LoadPlan)
        .options(selectinload(LoadPlan.connection))
        .where(LoadPlan.id == plan_id)
    )
    plan = result.scalar_one_or_none()
    if plan is None or plan.connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Load plan or its Salesforce connection not found",
        )

    try:
        access_token = await get_access_token(db, plan.connection)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not authenticate with Salesforce: {exc}",
        ) from exc

    try:
        explain_result = await explain_soql(
            plan.connection.instance_url,
            access_token,
            soql,
        )
    except BulkAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Salesforce explain endpoint error: {exc}",
        ) from exc

    return ValidateSoqlResponse(
        valid=explain_result.valid,
        plan=explain_result.plan if explain_result.valid else None,
        error=explain_result.error if not explain_result.valid else None,
    )


@router.post("/{plan_id}/steps/{step_id}/preview", response_model=StepPreviewResponse)
async def preview_step(
    plan_id: str,
    step_id: str,
    db: AsyncSession = Depends(get_db),
) -> StepPreviewResponse:
    """Discover CSV files matching the step's pattern and return row counts.

    For query/queryAll steps, no file discovery is performed.  Instead the
    SOQL is validated via the Salesforce explain endpoint and the result is
    returned in a shape-compatible envelope.
    """
    step = await _get_step_or_404(plan_id, step_id, db)

    # Query ops: validate SOQL via the explain endpoint.
    if step.operation in QUERY_OPERATIONS:
        # Load the plan with its Salesforce connection eagerly so we can call
        # get_access_token without a separate query.
        result = await db.execute(
            select(LoadPlan)
            .options(selectinload(LoadPlan.connection))
            .where(LoadPlan.id == plan_id)
        )
        plan = result.scalar_one_or_none()
        if plan is None or plan.connection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Load plan or its Salesforce connection not found",
            )

        soql = step.soql or ""
        if not soql:
            return StepPreviewResponse(
                kind="query",
                valid=False,
                error="No SOQL defined on this step",
                matched_files=[],
                total_rows=0,
            )

        try:
            access_token = await get_access_token(db, plan.connection)
        except AuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not authenticate with Salesforce: {exc}",
            ) from exc

        try:
            explain_result = await explain_soql(
                plan.connection.instance_url,
                access_token,
                soql,
            )
        except BulkAPIError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Salesforce explain endpoint error: {exc}",
            ) from exc

        return StepPreviewResponse(
            kind="query",
            valid=explain_result.valid,
            plan=explain_result.plan if explain_result.valid else None,
            error=explain_result.error if not explain_result.valid else None,
            matched_files=[],
            total_rows=0,
        )

    try:
        storage = await get_storage(step.input_connection_id or "local", db)
    except InputConnectionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UnsupportedInputProviderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except InputStorageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        matched_paths = storage.discover_files(step.csv_file_pattern)
    except InputStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    file_infos: List[FilePreviewInfo] = []
    total_rows = 0

    for filepath in matched_paths:
        row_count = 0
        try:
            with storage.open_text(filepath) as fh:
                reader = csv.reader(fh)
                next(reader, None)  # skip header
                row_count = sum(1 for _ in reader)
        except (FileNotFoundError, InputStorageError, OSError) as exc:
            logger.warning("Could not read %s for preview: %s", filepath, exc)
        file_infos.append(
            FilePreviewInfo(filename=pathlib.PurePosixPath(filepath).name, row_count=row_count)
        )
        total_rows += row_count

    return StepPreviewResponse(
        kind="dml",
        pattern=step.csv_file_pattern,
        matched_files=file_infos,
        total_rows=total_rows,
    )
