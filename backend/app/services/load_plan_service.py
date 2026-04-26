"""Load Plan domain services — plan duplication and run creation."""

import logging
import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep

logger = logging.getLogger(__name__)


# Columns the duplication routine must NOT copy verbatim. These are either
# generated server-side (id/timestamps), structural FKs that must be rewritten
# to point at the new plan, or fields with intentional duplication semantics
# (the new plan's name is prefixed "Copy of …").
_PLAN_EXCLUDED = {"id", "created_at", "updated_at", "name"}
_STEP_EXCLUDED = {"id", "created_at", "updated_at", "load_plan_id"}


def _copy_columns(source, exclude: set[str]) -> dict:
    """Return a dict of {column_name: source_value} for every mapped column
    on *source* that is not in *exclude*.

    Reading the column list from ``__table__.columns`` (rather than enumerating
    fields by hand) means any future column added to the model is automatically
    carried through duplication. The accompanying regression test
    (``test_duplicate_plan_copies_all_columns``) enforces this dynamically.
    """
    return {
        col.name: getattr(source, col.name)
        for col in source.__table__.columns
        if col.name not in exclude
    }


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
        name=f"Copy of {source.name}",
        **_copy_columns(source, _PLAN_EXCLUDED),
    )
    db.add(new_plan)
    await db.flush()  # populate new_plan.id before creating steps

    # Two-pass step copy so input_from_step_id (SFBL-166) can be remapped from
    # the source step IDs to the new clones' IDs. Pass 1 creates the new rows
    # with input_from_step_id=NULL so the FK stays valid; pass 2 patches the
    # references using the source→clone id map.
    #
    # Snapshot the (source_step_id, source_input_from_step_id) tuples up front
    # so subsequent flushes can't expire the source attributes out from under
    # us before we read them back.
    source_snapshots: list[tuple[str, Optional[str]]] = [
        (step.id, step.input_from_step_id) for step in source.load_steps
    ]

    # Pre-generate the new step IDs in Python so the source→clone mapping is
    # known before the rows are flushed. SQLAlchemy column-level ``default=``
    # callables only fire at flush time, so reading ``new_step.id`` immediately
    # after ``db.add()`` would yield None.
    id_map: dict[str, str] = {
        src_id: str(uuid.uuid4()) for src_id, _ in source_snapshots
    }
    new_steps: list[LoadStep] = []
    for step in source.load_steps:
        cloned_columns = _copy_columns(step, _STEP_EXCLUDED)
        cloned_columns["input_from_step_id"] = None
        new_step = LoadStep(
            id=id_map[step.id],
            load_plan_id=new_plan.id,
            **cloned_columns,
        )
        db.add(new_step)
        new_steps.append(new_step)

    await db.flush()  # ensure new step rows exist before patching FKs

    for (_, source_input_from), new_step in zip(source_snapshots, new_steps):
        if source_input_from is not None:
            new_step.input_from_step_id = id_map.get(source_input_from)

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
