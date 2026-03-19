import json
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, computed_field, field_validator

from app.models.load_run import RunStatus
from app.schemas.job import JobResponse


class RunErrorSummary(BaseModel):
    """Typed structure for run-level error context stored in LoadRun.error_summary."""

    auth_error: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


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
    error_summary: Optional[RunErrorSummary] = None
    retry_of_run_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("error_summary", mode="before")
    @classmethod
    def _parse_error_summary(cls, v: Any) -> Any:
        """Parse JSON string from the DB column into a dict for Pydantic to validate."""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, ValueError):
                return None
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_retry(self) -> bool:
        """True when this run was created as a step retry of another run."""
        return self.retry_of_run_id is not None


class LoadRunDetailResponse(LoadRunResponse):
    """Run details with full job breakdown."""

    jobs: List[JobResponse] = []


