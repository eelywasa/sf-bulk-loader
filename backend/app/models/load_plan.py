import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.connection import Connection
    from app.models.load_run import LoadRun
    from app.models.load_step import LoadStep


class LoadPlan(Base):
    __tablename__ = "load_plan"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    connection_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("connection.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    abort_on_step_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_threshold_pct: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    max_parallel_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )

    connection: Mapped["Connection"] = relationship("Connection", back_populates="load_plans")
    load_steps: Mapped[list["LoadStep"]] = relationship(
        "LoadStep", back_populates="load_plan", cascade="all, delete-orphan", order_by="LoadStep.sequence"
    )
    load_runs: Mapped[list["LoadRun"]] = relationship("LoadRun", back_populates="load_plan")
