import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.job import JobRecord
    from app.models.load_plan import LoadPlan


class RunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    completed_with_errors = "completed_with_errors"
    failed = "failed"
    aborted = "aborted"


class LoadRun(Base):
    __tablename__ = "load_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    load_plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("load_plan.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, name="run_status_enum"), nullable=False, default=RunStatus.pending
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    total_records: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_success: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_errors: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    initiated_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # JSON string: per-step error summary
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Back-reference to the run this is a retry of (nullable)
    retry_of_run_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("load_run.id"), nullable=True
    )

    load_plan: Mapped["LoadPlan"] = relationship("LoadPlan", back_populates="load_runs")
    job_records: Mapped[list["JobRecord"]] = relationship("JobRecord", back_populates="load_run")
