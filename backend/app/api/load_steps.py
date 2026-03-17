"""Load Steps API — manage steps within a load plan."""

import csv
import glob
import logging
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.load_plan import LoadPlan
from app.models.load_step import LoadStep
from app.services.auth import get_current_user
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


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.post("/{plan_id}/steps", response_model=LoadStepResponse, status_code=status.HTTP_201_CREATED)
async def add_step(
    plan_id: str,
    data: LoadStepCreate,
    db: AsyncSession = Depends(get_db),
) -> LoadStep:
    await _get_plan_or_404(plan_id, db)
    step_data = data.model_dump()
    if step_data.get("sequence") is None:
        result = await db.execute(
            select(func.max(LoadStep.sequence)).where(LoadStep.load_plan_id == plan_id)
        )
        max_seq: Optional[int] = result.scalar()
        step_data["sequence"] = (max_seq or 0) + 1
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

    result = await db.execute(select(LoadStep).where(LoadStep.load_plan_id == plan_id))
    existing = {step.id: step for step in result.scalars().all()}

    if set(data.step_ids) != set(existing.keys()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="step_ids must contain exactly the IDs of all steps in this plan",
        )

    for new_seq, step_id in enumerate(data.step_ids, start=1):
        existing[step_id].sequence = new_seq

    await db.commit()

    result = await db.execute(
        select(LoadStep)
        .where(LoadStep.load_plan_id == plan_id)
        .order_by(LoadStep.sequence)
    )
    return list(result.scalars().all())


@router.put("/{plan_id}/steps/{step_id}", response_model=LoadStepResponse)
async def update_step(
    plan_id: str,
    step_id: str,
    data: LoadStepUpdate,
    db: AsyncSession = Depends(get_db),
) -> LoadStep:
    step = await _get_step_or_404(plan_id, step_id, db)
    for field, value in data.model_dump(exclude_unset=True).items():
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

    pattern = os.path.join(settings.input_dir, step.csv_file_pattern)
    matched_paths = sorted(glob.glob(pattern))

    file_infos: List[FilePreviewInfo] = []
    total_rows = 0

    for filepath in matched_paths:
        filename = os.path.basename(filepath)
        row_count = 0
        try:
            with open(filepath, newline="", encoding="utf-8-sig") as fh:
                reader = csv.reader(fh)
                next(reader, None)  # skip header
                row_count = sum(1 for _ in reader)
        except OSError as exc:
            logger.warning("Could not read %s for preview: %s", filepath, exc)
        file_infos.append(FilePreviewInfo(filename=filename, row_count=row_count))
        total_rows += row_count

    return StepPreviewResponse(
        pattern=step.csv_file_pattern,
        matched_files=file_infos,
        total_rows=total_rows,
    )
