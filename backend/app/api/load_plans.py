"""Load Plans API — CRUD for load plan configurations."""

import logging
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.connection import Connection
from app.models.input_connection import InputConnection
from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun
from app.models.user import User
from app.schemas.load_plan import (
    LoadPlanCreate,
    LoadPlanListResponse,
    LoadPlanResponse,
    LoadPlanUpdate,
)
from app.schemas.load_run import LoadRunResponse
from app.services import orchestrator
from app.services.auth import get_current_user
from app.services import load_plan_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/load-plans", tags=["load-plans"], dependencies=[Depends(get_current_user)])


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _get_plan_with_steps(plan_id: str, db: AsyncSession) -> LoadPlan:
    result = await db.execute(
        select(LoadPlan)
        .where(LoadPlan.id == plan_id)
        .options(selectinload(LoadPlan.load_steps))
    )
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Load plan not found")
    return plan


# ── Routes ─────────────────────────────────────────────────────────────────────


@router.get("/", response_model=List[LoadPlanListResponse])
async def list_load_plans(db: AsyncSession = Depends(get_db)) -> List[LoadPlan]:
    result = await db.execute(select(LoadPlan).order_by(LoadPlan.created_at.desc()))
    return list(result.scalars().all())


async def _validate_output_connection(output_connection_id: str, db: AsyncSession) -> None:
    """Raise 422 if output_connection_id does not reference a connection with direction out or both."""
    ic = await db.get(InputConnection, output_connection_id)
    if ic is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Input connection '{output_connection_id}' not found",
        )
    if ic.direction not in ("out", "both"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Storage connection '{output_connection_id}' has direction '{ic.direction}' "
                "but must be 'out' or 'both' to be used as an output destination"
            ),
        )


@router.post("/", response_model=LoadPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_load_plan(data: LoadPlanCreate, db: AsyncSession = Depends(get_db)) -> LoadPlan:
    # Validate the referenced Salesforce connection exists
    conn = await db.get(Connection, data.connection_id)
    if conn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

    # Validate output storage connection direction
    if data.output_connection_id is not None:
        await _validate_output_connection(data.output_connection_id, db)

    plan = LoadPlan(**data.model_dump())
    db.add(plan)
    await db.commit()

    # Re-query with steps loaded for the response
    return await _get_plan_with_steps(plan.id, db)


@router.get("/{plan_id}", response_model=LoadPlanResponse)
async def get_load_plan(plan_id: str, db: AsyncSession = Depends(get_db)) -> LoadPlan:
    return await _get_plan_with_steps(plan_id, db)


@router.put("/{plan_id}", response_model=LoadPlanResponse)
async def update_load_plan(
    plan_id: str,
    data: LoadPlanUpdate,
    db: AsyncSession = Depends(get_db),
) -> LoadPlan:
    plan = await _get_plan_with_steps(plan_id, db)
    update_data = data.model_dump(exclude_unset=True)

    if "connection_id" in update_data:
        conn = await db.get(Connection, update_data["connection_id"])
        if conn is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

    if "output_connection_id" in update_data and update_data["output_connection_id"] is not None:
        await _validate_output_connection(update_data["output_connection_id"], db)

    for field, value in update_data.items():
        setattr(plan, field, value)

    await db.commit()
    return await _get_plan_with_steps(plan_id, db)


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_load_plan(plan_id: str, db: AsyncSession = Depends(get_db)) -> None:
    plan = await db.get(LoadPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Load plan not found")
    await db.delete(plan)
    await db.commit()


@router.post("/{plan_id}/duplicate", response_model=LoadPlanResponse, status_code=status.HTTP_201_CREATED)
async def duplicate_load_plan(plan_id: str, db: AsyncSession = Depends(get_db)) -> LoadPlan:
    """Create a copy of an existing load plan including all its steps."""
    return await load_plan_service.duplicate_plan(db, plan_id)


@router.post("/{plan_id}/run", response_model=LoadRunResponse, status_code=status.HTTP_201_CREATED)
async def start_load_run(
    plan_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LoadRun:
    """Create a new Load Run for a plan and enqueue it for background execution."""
    run = await load_plan_service.create_run(db, plan_id, current_user.username)
    background_tasks.add_task(orchestrator.execute_run, run.id)
    return run
