import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InputConnection(Base):
    __tablename__ = "input_connection"
    __table_args__ = (
        CheckConstraint("direction IN ('in', 'out', 'both')", name="ck_input_connection_direction"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # 's3'
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    root_prefix: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Stored encrypted via Fernet (ENCRYPTION_KEY env var)
    access_key_id: Mapped[str] = mapped_column(Text, nullable=False)
    secret_access_key: Mapped[str] = mapped_column(Text, nullable=False)
    session_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="in")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )
