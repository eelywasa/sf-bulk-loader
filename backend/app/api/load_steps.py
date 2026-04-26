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
from app.models.user import User
from app.auth.permissions import PLANS_MANAGE, PLANS_VIEW, require_permission
from app.services.auth import get_current_user
from app.services.input_storage import (
    InputConnectionNotFoundError,
    InputStorageError,
    LOCAL_OUTPUT_SOURCE,
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
    _validate_input_source_exclusivity,
    _validate_query_dml_fields,
)

logger = logging.getLogger(__name__)

_require_view = require_permission(PLANS_VIEW)
_require_manage = require_permission(PLANS_MANAGE)

# Steps are nested under /api/load-plans
router = APIRouter(prefix="/api/load-plans", tags=["load-steps"], dependencies=[Depends(_require_view)])


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
    # The local output tree is always read-safe; skip DB lookup.
    if input_connection_id == LOCAL_OUTPUT_SOURCE:
        return
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
    _manage: User = Depends(_require_manage),
) -> LoadStep:
    await _get_plan_or_404(plan_id, db)

    if data.input_connection_id is not None:
        await _validate_input_connection_direction(data.input_connection_id, db)

    step_data = data.model_dump()
    if step_data.get("sequence") is None:
        step_data["sequence"] = await load_step_service.next_sequence(db, plan_id)

    await load_step_service.validate_step_input_reference(
        db,
        plan_id,
        input_from_step_id=step_data.get("input_from_step_id"),
        own_sequence=step_data["sequence"],
    )
    await load_step_service.validate_unique_step_name(
        db, plan_id, name=step_data.get("name")
    )

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
    _manage: User = Depends(_require_manage),
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
    _manage: User = Depends(_require_manage),
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
    effective_input_from_step_id = (
        update_data["input_from_step_id"]
        if "input_from_step_id" in update_data
        else step.input_from_step_id
    )
    try:
        _validate_query_dml_fields(
            effective_operation,
            effective_soql,
            effective_pattern,
            input_from_step_id=effective_input_from_step_id,
            context="update",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    # SFBL-166: validate the merged effective input-source state — a partial
    # update may set input_from_step_id while leaving an existing
    # csv_file_pattern / input_connection_id on the row.
    effective_input_from = effective_input_from_step_id
    effective_input_conn = (
        update_data["input_connection_id"]
        if "input_connection_id" in update_data
        else step.input_connection_id
    )
    try:
        _validate_input_source_exclusivity(
            effective_input_from, effective_pattern, effective_input_conn
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    effective_sequence = update_data.get("sequence", step.sequence)
    # Re-run reference validation whenever the effective state carries an
    # input_from_step_id, not only when the patch sets one. Otherwise a
    # client can update just `sequence` on a step that already has a
    # reference and invert the dependency order without hitting the 422
    # — leaving a persisted plan where the downstream step's sequence is
    # ≤ its upstream's.
    if effective_input_from is not None:
        await load_step_service.validate_step_input_reference(
            db,
            plan_id,
            input_from_step_id=effective_input_from,
            own_sequence=effective_sequence,
            own_step_id=step.id,
        )
    if "name" in update_data:
        await load_step_service.validate_unique_step_name(
            db, plan_id, name=update_data["name"], own_step_id=step.id
        )

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
    _manage: User = Depends(_require_manage),
) -> None:
    step = await _get_step_or_404(plan_id, step_id, db)

    # SFBL-166: block deletion when any downstream step in this plan depends
    # on this step via input_from_step_id. Same pattern as reorder validation
    # — surface the dependents so the user can clear/repoint before deleting.
    # (The FK is ON DELETE SET NULL as a defensive last resort, but silently
    # nulling out a downstream step's input source is poor UX.)
    dep_result = await db.execute(
        select(LoadStep).where(
            LoadStep.load_plan_id == plan_id,
            LoadStep.input_from_step_id == step_id,
        )
    )
    dependents = list(dep_result.scalars().all())
    if dependents:
        labels = ", ".join(
            d.name or f"Step {d.sequence}: {d.operation.value} {d.object_name}"
            for d in dependents
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Cannot delete this step — it is the input source for: {labels}. "
                "Clear or repoint the downstream step's input source first."
            ),
        )

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

    # SFBL-166: a DML step that consumes an upstream query step's output has
    # neither a csv_file_pattern nor an input_connection_id — there is nothing
    # to discover at preview time because the artefact is produced when the
    # run executes. Return a descriptive note and bail out before discovery.
    if step.input_from_step_id:
        upstream = await db.get(LoadStep, step.input_from_step_id)
        if upstream is None:
            label = f"step {step.input_from_step_id}"
        else:
            label = (
                upstream.name
                or f"Step {upstream.sequence}: {upstream.operation.value} {upstream.object_name}"
            )
        return StepPreviewResponse(
            kind="dml",
            pattern=None,
            matched_files=[],
            total_rows=0,
            note=f"Input resolved at run time from upstream step: {label}",
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
