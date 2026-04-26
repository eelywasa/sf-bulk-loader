from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.models.load_step import Operation, QUERY_OPERATIONS, DML_OPERATIONS


def _normalize_name(value: Optional[str]) -> Optional[str]:
    """Trim whitespace; treat empty/whitespace-only strings as NULL.

    Keeps the partial unique index on ``(load_plan_id, name)`` from rejecting
    plans where multiple steps were saved with an empty-string name field.
    """
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _validate_input_source_exclusivity(
    input_from_step_id: Optional[str],
    csv_file_pattern: Optional[str],
    input_connection_id: Optional[str],
) -> None:
    """SFBL-166: input_from_step_id is mutually exclusive with the other two
    input-source fields.

    Run together with (not as a replacement for) the operation/soql/csv check
    in ``_validate_query_dml_fields`` — operation-level rules apply regardless
    of whether the upstream is a step reference or a file pattern.
    """
    if input_from_step_id is None:
        return
    if csv_file_pattern is not None:
        raise ValueError(
            "'input_from_step_id' and 'csv_file_pattern' cannot both be set — "
            "a step's input source is either an upstream step or a file pattern, not both"
        )
    if input_connection_id is not None:
        raise ValueError(
            "'input_from_step_id' cannot be combined with 'input_connection_id' — "
            "the upstream step's output backend determines the source location"
        )


def _validate_query_dml_fields(
    operation: Optional[Operation],
    soql: Optional[str],
    csv_file_pattern: Optional[str],
    *,
    input_from_step_id: Optional[str] = None,
    context: str = "",
) -> None:
    """Shared validation logic for soql / csv_file_pattern / input_from_step_id
    against the operation.

    Raises ValueError if the combination is invalid. SFBL-166: a DML step may
    omit ``csv_file_pattern`` when ``input_from_step_id`` is set, because the
    upstream query step's output supplies the CSV.
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
        if input_from_step_id is not None:
            raise ValueError(
                f"'input_from_step_id' must not be set when operation is '{operation.value}' — "
                "query ops do not consume upstream step output"
            )
    else:
        # DML operation
        if not csv_file_pattern and not input_from_step_id:
            raise ValueError(
                f"'csv_file_pattern' is required when operation is '{operation.value}'"
                " (or set 'input_from_step_id' to consume an upstream query step)"
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
    partition_size: Optional[int] = None
    assignment_rule_id: Optional[str] = None
    input_connection_id: Optional[str] = None
    name: Optional[str] = None
    input_from_step_id: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name_field(cls, value):  # noqa: ANN001 — pydantic validator
        return _normalize_name(value) if isinstance(value, str) or value is None else value

    @model_validator(mode="after")
    def _cross_validate_query_dml(self) -> "LoadStepBase":
        _validate_query_dml_fields(
            self.operation,
            self.soql,
            self.csv_file_pattern,
            input_from_step_id=self.input_from_step_id,
        )
        _validate_input_source_exclusivity(
            self.input_from_step_id, self.csv_file_pattern, self.input_connection_id
        )
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
    name: Optional[str] = None
    input_from_step_id: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name_field(cls, value):  # noqa: ANN001 — pydantic validator
        return _normalize_name(value) if isinstance(value, str) or value is None else value

    @model_validator(mode="after")
    def _cross_validate_query_dml(self) -> "LoadStepUpdate":
        # Only validate when operation is provided (partial updates may omit it).
        _validate_query_dml_fields(
            self.operation,
            self.soql,
            self.csv_file_pattern,
            input_from_step_id=self.input_from_step_id,
        )
        # Note: cross-source exclusivity is checked at the API layer against
        # the merged effective state (the patch may clear one field while
        # leaving the others persistent on the row).
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


class ValidateSoqlRequest(BaseModel):
    soql: str


class ValidateSoqlResponse(BaseModel):
    valid: bool
    plan: Optional[dict] = None
    error: Optional[str] = None


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
