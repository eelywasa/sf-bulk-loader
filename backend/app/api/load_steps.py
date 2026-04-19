"""Load Steps API — manage steps within a load plan."""

import csv
import logging
import pathlib
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.input_connection import InputConnection
from app.models.load_plan import LoadPlan
from app.models.load_step import LoadStep
from app.services.auth import get_current_user
from app.services.input_storage import (
    InputConnectionNotFoundError,
    InputStorageError,
    UnsupportedInputProviderError,
    get_storage,
)
from app.services import load_step_service
from app.schemas.load_step import (
    FilePreviewInfo,
    LoadStepCreate,
    LoadStepResponse,
    LoadStepUpdate,
    StepPreviewResponse,
    StepReorderRequest,
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


@router.post("/{plan_id}/steps/{step_id}/preview", response_model=StepPreviewResponse)
async def preview_step(
    plan_id: str,
    step_id: str,
    db: AsyncSession = Depends(get_db),
) -> StepPreviewResponse:
    """Discover CSV files matching the step's pattern and return row counts."""
    step = await _get_step_or_404(plan_id, step_id, db)

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
        pattern=step.csv_file_pattern,
        matched_files=file_infos,
        total_rows=total_rows,
    )
