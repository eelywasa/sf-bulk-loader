from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from app.models.load_step import Operation


class LoadStepBase(BaseModel):
    sequence: int
    object_name: str
    operation: Operation
    external_id_field: Optional[str] = None
    csv_file_pattern: str
    partition_size: int = 10_000
    assignment_rule_id: Optional[str] = None


class LoadStepCreate(LoadStepBase):
    pass


class LoadStepUpdate(BaseModel):
    sequence: Optional[int] = None
    object_name: Optional[str] = None
    operation: Optional[Operation] = None
    external_id_field: Optional[str] = None
    csv_file_pattern: Optional[str] = None
    partition_size: Optional[int] = None
    assignment_rule_id: Optional[str] = None


class LoadStepResponse(LoadStepBase):
    id: str
    load_plan_id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StepReorderRequest(BaseModel):
    step_ids: List[str]  # Ordered list of step IDs; length must match plan's step count


class FilePreviewInfo(BaseModel):
    filename: str
    row_count: int


class StepPreviewResponse(BaseModel):
    pattern: str
    matched_files: List[FilePreviewInfo]
    total_rows: int
