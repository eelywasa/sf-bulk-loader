"""Load Step domain services — sequence assignment, step reordering, and
cross-step input-reference validation (SFBL-166)."""

from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.load_step import LoadStep, QUERY_OPERATIONS


async def next_sequence(db: AsyncSession, plan_id: str) -> int:
    """Return the next sequence number for a new step in the given plan."""
    result = await db.execute(
        select(func.max(LoadStep.sequence)).where(LoadStep.load_plan_id == plan_id)
    )
    max_seq: int | None = result.scalar()
    return (max_seq or 0) + 1


async def validate_step_input_reference(
    db: AsyncSession,
    plan_id: str,
    *,
    input_from_step_id: Optional[str],
    own_sequence: Optional[int],
    own_step_id: Optional[str] = None,
) -> None:
    """Validate that ``input_from_step_id`` is a legal upstream reference.

    Raises HTTPException(422) if the reference is invalid. ``own_step_id`` is
    None on create (no row yet) and the existing row's id on update.
    """
    if input_from_step_id is None:
        return

    if own_step_id is not None and input_from_step_id == own_step_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="'input_from_step_id' cannot reference the step itself",
        )

    referenced = await db.get(LoadStep, input_from_step_id)
    if referenced is None or referenced.load_plan_id != plan_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Referenced step '{input_from_step_id}' does not exist in this plan"
            ),
        )

    if referenced.operation not in QUERY_OPERATIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Referenced step '{input_from_step_id}' has operation "
                f"'{referenced.operation.value}'; only query/queryAll steps "
                "may be used as an input source"
            ),
        )

    if own_sequence is not None and referenced.sequence >= own_sequence:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Referenced step '{input_from_step_id}' has sequence "
                f"{referenced.sequence} which is not strictly less than this step's "
                f"sequence {own_sequence}"
            ),
        )


async def validate_unique_step_name(
    db: AsyncSession,
    plan_id: str,
    *,
    name: Optional[str],
    own_step_id: Optional[str] = None,
) -> None:
    """Reject creating/updating a step whose ``name`` collides within the plan.

    NULL names never collide. The DB-level partial unique index is the final
    guard, but raising 422 here gives a clean error message instead of a
    constraint-violation 500.
    """
    if name is None:
        return
    stmt = select(LoadStep.id).where(
        LoadStep.load_plan_id == plan_id, LoadStep.name == name
    )
    if own_step_id is not None:
        stmt = stmt.where(LoadStep.id != own_step_id)
    result = await db.execute(stmt)
    if result.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"A step named '{name}' already exists in this plan",
        )


async def reorder_steps(db: AsyncSession, plan_id: str, step_ids: list[str]) -> list[LoadStep]:
    """Reassign step sequences based on the ordered list of step IDs.

    Raises 400 on mismatch. Raises 422 if any existing ``input_from_step_id``
    reference would be inverted (or become a self-reference) by the new
    ordering — the user must clear or repoint those references manually
    before retrying.
    """
    result = await db.execute(select(LoadStep).where(LoadStep.load_plan_id == plan_id))
    existing = {step.id: step for step in result.scalars().all()}

    if set(step_ids) != set(existing.keys()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="step_ids must contain exactly the IDs of all steps in this plan",
        )

    # Compute the proposed new sequences without mutating the rows yet — the
    # ORM session would otherwise hold a partial new ordering even if we abort.
    proposed_sequence = {step_id: idx for idx, step_id in enumerate(step_ids, start=1)}

    offending: list[tuple[str, str]] = []
    for step in existing.values():
        ref_id = step.input_from_step_id
        if ref_id is None:
            continue
        # Self-references are guarded at create/update time, but check again
        # here defensively in case bad data exists.
        if ref_id == step.id or proposed_sequence.get(ref_id, 0) >= proposed_sequence[step.id]:
            offending.append((step.id, ref_id))

    if offending:
        pairs = ", ".join(f"({a} → {b})" for a, b in offending)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Reorder rejected: the following step references would be inverted "
                f"by the new ordering {pairs}. Clear or repoint these references first."
            ),
        )

    for step_id, new_seq in proposed_sequence.items():
        existing[step_id].sequence = new_seq

    await db.commit()

    result = await db.execute(
        select(LoadStep)
        .where(LoadStep.load_plan_id == plan_id)
        .order_by(LoadStep.sequence)
    )
    return list(result.scalars().all())
