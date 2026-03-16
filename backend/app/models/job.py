import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.load_run import LoadRun
    from app.models.load_step import LoadStep


class JobStatus(str, enum.Enum):
    pending = "pending"
    uploading = "uploading"
    upload_complete = "upload_complete"
    in_progress = "in_progress"
    job_complete = "job_complete"
    failed = "failed"
    aborted = "aborted"


class JobRecord(Base):
    __tablename__ = "job_record"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    load_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("load_run.id", ondelete="CASCADE"), nullable=False
    )
    load_step_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("load_step.id", ondelete="RESTRICT"), nullable=False
    )
    # Salesforce Bulk API 2.0 job ID (18-char)
    sf_job_id: Mapped[Optional[str]] = mapped_column(String(18), nullable=True)
    partition_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status_enum"), nullable=False, default=JobStatus.pending
    )
    records_processed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    records_failed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_records: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Relative paths (relative to OUTPUT_DIR)
    success_file_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    error_file_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    unprocessed_file_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # JSON string of last Salesforce API response
    sf_api_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    load_run: Mapped["LoadRun"] = relationship("LoadRun", back_populates="job_records")
    load_step: Mapped["LoadStep"] = relationship("LoadStep", back_populates="job_records")
