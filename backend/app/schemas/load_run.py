from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from app.models.load_run import RunStatus
from app.schemas.job import JobResponse


class LoadRunResponse(BaseModel):
    id: str
    load_plan_id: str
    status: RunStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_records: Optional[int] = None
    total_success: Optional[int] = None
    total_errors: Optional[int] = None
    initiated_by: Optional[str] = None
    error_summary: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class LoadRunDetailResponse(LoadRunResponse):
    """Run details with full job breakdown."""

    jobs: List[JobResponse] = []


class RunSummaryStepStats(BaseModel):
    step_id: str
    object_name: str
    sequence: int
    total_records: int
    total_success: int
    total_errors: int
    job_count: int


class RunSummaryResponse(BaseModel):
    run_id: str
    status: RunStatus
    total_records: int
    total_success: int
    total_errors: int
    steps: List[RunSummaryStepStats]
