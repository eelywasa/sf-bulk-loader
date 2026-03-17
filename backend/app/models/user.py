import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "user"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Local auth
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # SAML / profile
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    saml_name_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # Shared
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )
