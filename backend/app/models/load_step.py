import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.input_connection import InputConnection
    from app.models.job import JobRecord
    from app.models.load_plan import LoadPlan


class Operation(str, enum.Enum):
    insert = "insert"
    update = "update"
    upsert = "upsert"
    delete = "delete"
    query = "query"
    queryAll = "queryAll"


# Convenience sets used by validators
QUERY_OPERATIONS: frozenset[Operation] = frozenset({Operation.query, Operation.queryAll})
DML_OPERATIONS: frozenset[Operation] = frozenset({
    Operation.insert, Operation.update, Operation.upsert, Operation.delete
})


class LoadStep(Base):
    __tablename__ = "load_step"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    load_plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("load_plan.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    object_name: Mapped[str] = mapped_column(String(255), nullable=False)
    operation: Mapped[Operation] = mapped_column(
        SAEnum(Operation, name="operation_enum"), nullable=False
    )
    # Required when operation == upsert
    external_id_field: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Required for DML operations; null for query ops
    csv_file_pattern: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # Required for query/queryAll operations; null for DML ops
    soql: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # partition_size: None means "use the DB-backed default_partition_size setting"
    # (SFBL-156). New steps should omit this field to inherit the live default.
    partition_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    assignment_rule_id: Mapped[Optional[str]] = mapped_column(String(18), nullable=True)
    # Loosely-typed source identifier: None/""/"local" → local input tree,
    # "local-output" → local output tree (SFBL-178), else an InputConnection
    # UUID.  Not a DB-level FK — resolution happens at request time in
    # app.services.input_storage.get_storage.
    input_connection_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )

    load_plan: Mapped["LoadPlan"] = relationship("LoadPlan", back_populates="load_steps")
    job_records: Mapped[list["JobRecord"]] = relationship("JobRecord", back_populates="load_step")
