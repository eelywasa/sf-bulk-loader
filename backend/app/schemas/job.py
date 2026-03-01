from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.job import JobStatus


class JobResponse(BaseModel):
    id: str
    load_run_id: str
    load_step_id: str
    sf_job_id: Optional[str] = None
    partition_index: int
    status: JobStatus
    records_processed: Optional[int] = None
    records_failed: Optional[int] = None
    success_file_path: Optional[str] = None
    error_file_path: Optional[str] = None
    unprocessed_file_path: Optional[str] = None
    sf_api_response: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
