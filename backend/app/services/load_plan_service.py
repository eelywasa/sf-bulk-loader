"""Load Plan domain services — plan duplication and run creation."""

import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep

logger = logging.getLogger(__name__)


async def duplicate_plan(db: AsyncSession, plan_id: str) -> LoadPlan:
    """Deep-copy a load plan and all its steps. Returns the new plan with steps loaded."""
    result = await db.execute(
        select(LoadPlan)
        .where(LoadPlan.id == plan_id)
        .options(selectinload(LoadPlan.load_steps))
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Load plan not found")

    new_plan = LoadPlan(
        connection_id=source.connection_id,
        name=f"Copy of {source.name}",
        description=source.description,
        abort_on_step_failure=source.abort_on_step_failure,
        error_threshold_pct=source.error_threshold_pct,
        max_parallel_jobs=source.max_parallel_jobs,
    )
    db.add(new_plan)
    await db.flush()  # populate new_plan.id before creating steps

    for step in source.load_steps:
        db.add(LoadStep(
            load_plan_id=new_plan.id,
            sequence=step.sequence,
            object_name=step.object_name,
            operation=step.operation,
            external_id_field=step.external_id_field,
            csv_file_pattern=step.csv_file_pattern,
            partition_size=step.partition_size,
            assignment_rule_id=step.assignment_rule_id,
        ))

    await db.commit()

    result = await db.execute(
        select(LoadPlan)
        .where(LoadPlan.id == new_plan.id)
        .options(selectinload(LoadPlan.load_steps))
    )
    return result.scalar_one()


async def create_run(db: AsyncSession, plan_id: str, initiated_by: str) -> LoadRun:
    """Create a pending LoadRun for the given plan. Does not enqueue execution."""
    plan = await db.get(LoadPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Load plan not found")

    run = LoadRun(
        load_plan_id=plan_id,
        status=RunStatus.pending,
        initiated_by=initiated_by,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    logger.info("Load run %s created for plan %s (initiated_by=%s)", run.id, plan_id, initiated_by)
    return run
