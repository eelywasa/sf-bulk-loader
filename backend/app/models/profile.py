import uuid
from datetime import datetime
from functools import cached_property
from typing import List

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.profile_permission import ProfilePermission


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.true())
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=sa.func.now()
    )

    permissions: Mapped[List[ProfilePermission]] = relationship(
        "ProfilePermission",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @cached_property
    def permission_keys(self) -> frozenset[str]:
        """Frozenset of permission keys for O(1) membership checks.

        Uses cached_property so the set is built once per Profile instance.
        Cache is invalidated automatically when the object is refreshed
        (SQLAlchemy clears instance __dict__ on expiry).
        """
        return frozenset(p.permission_key for p in self.permissions)
