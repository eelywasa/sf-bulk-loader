from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.load_step import Operation, QUERY_OPERATIONS, DML_OPERATIONS


def _validate_query_dml_fields(operation: Optional[Operation], soql: Optional[str], csv_file_pattern: Optional[str], *, context: str = "") -> None:
    """Shared validation logic for soql / csv_file_pattern against the operation.

    Raises ValueError if the combination is invalid.
    """
    if operation is None:
        # No operation provided (partial update), skip cross-field validation.
        return

    if operation in QUERY_OPERATIONS:
        if not soql:
            raise ValueError(
                f"'soql' is required when operation is '{operation.value}'"
                + (f" ({context})" if context else "")
            )
        if csv_file_pattern is not None:
            raise ValueError(
                f"'csv_file_pattern' must not be set when operation is '{operation.value}' — "
                "query ops do not consume CSV files"
            )
    else:
        # DML operation
        if not csv_file_pattern:
            raise ValueError(
                f"'csv_file_pattern' is required when operation is '{operation.value}'"
                + (f" ({context})" if context else "")
            )
        if soql is not None:
            raise ValueError(
                f"'soql' must not be set when operation is '{operation.value}' — "
                "only query ops use SOQL"
            )


class LoadStepBase(BaseModel):
    sequence: int
    object_name: str
    operation: Operation
    external_id_field: Optional[str] = None
    csv_file_pattern: Optional[str] = None
    soql: Optional[str] = None
    partition_size: int = 10_000
    assignment_rule_id: Optional[str] = None
    input_connection_id: Optional[str] = None

    @model_validator(mode="after")
    def _cross_validate_query_dml(self) -> "LoadStepBase":
        _validate_query_dml_fields(self.operation, self.soql, self.csv_file_pattern)
        return self


class LoadStepCreate(LoadStepBase):
    sequence: Optional[int] = None


class LoadStepUpdate(BaseModel):
    sequence: Optional[int] = None
    object_name: Optional[str] = None
    operation: Optional[Operation] = None
    external_id_field: Optional[str] = None
    csv_file_pattern: Optional[str] = None
    soql: Optional[str] = None
    partition_size: Optional[int] = None
    assignment_rule_id: Optional[str] = None
    input_connection_id: Optional[str] = None

    @model_validator(mode="after")
    def _cross_validate_query_dml(self) -> "LoadStepUpdate":
        # Only validate when operation is provided (partial updates may omit it).
        _validate_query_dml_fields(self.operation, self.soql, self.csv_file_pattern)
        return self


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
    pattern: Optional[str] = None
    matched_files: List[FilePreviewInfo] = []
    total_rows: int = 0
    kind: str = "dml"
    note: Optional[str] = None
    # Query-op explain fields (present only when kind="query")
    valid: Optional[bool] = None
    plan: Optional[dict] = None
    error: Optional[str] = None
