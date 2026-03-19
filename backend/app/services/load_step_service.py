"""Load Step domain services — sequence assignment and step reordering."""

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.load_step import LoadStep


async def next_sequence(db: AsyncSession, plan_id: str) -> int:
    """Return the next sequence number for a new step in the given plan."""
    result = await db.execute(
        select(func.max(LoadStep.sequence)).where(LoadStep.load_plan_id == plan_id)
    )
    max_seq: int | None = result.scalar()
    return (max_seq or 0) + 1


async def reorder_steps(db: AsyncSession, plan_id: str, step_ids: list[str]) -> list[LoadStep]:
    """Reassign step sequences based on the ordered list of step IDs. Raises 400 on mismatch."""
    result = await db.execute(select(LoadStep).where(LoadStep.load_plan_id == plan_id))
    existing = {step.id: step for step in result.scalars().all()}

    if set(step_ids) != set(existing.keys()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="step_ids must contain exactly the IDs of all steps in this plan",
        )

    for new_seq, step_id in enumerate(step_ids, start=1):
        existing[step_id].sequence = new_seq

    await db.commit()

    result = await db.execute(
        select(LoadStep)
        .where(LoadStep.load_plan_id == plan_id)
        .order_by(LoadStep.sequence)
    )
    return list(result.scalars().all())
