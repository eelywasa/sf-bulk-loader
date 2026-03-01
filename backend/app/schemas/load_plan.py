from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.load_step import LoadStepResponse


class LoadPlanBase(BaseModel):
    connection_id: str
    name: str
    description: Optional[str] = None
    abort_on_step_failure: bool = True
    error_threshold_pct: float = 10.0
    max_parallel_jobs: int = 5


class LoadPlanCreate(LoadPlanBase):
    pass


class LoadPlanUpdate(BaseModel):
    connection_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    abort_on_step_failure: Optional[bool] = None
    error_threshold_pct: Optional[float] = None
    max_parallel_jobs: Optional[int] = None


class LoadPlanListResponse(LoadPlanBase):
    id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LoadPlanResponse(LoadPlanListResponse):
    load_steps: List[LoadStepResponse] = []
