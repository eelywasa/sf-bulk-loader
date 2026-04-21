"""Pydantic schemas for the settings API (SFBL-154)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, RootModel


class SettingValue(BaseModel):
    """The value and metadata for a single setting key."""

    key: str
    value: Any
    type: str
    is_secret: bool
    description: str
    restart_required: bool
    updated_at: datetime | None = None


class CategorySettings(BaseModel):
    """All settings belonging to one category."""

    category: str
    settings: list[SettingValue]


class AllSettings(BaseModel):
    """All settings grouped by category."""

    categories: list[CategorySettings]


class PatchRequest(RootModel[dict[str, Any]]):
    """Free-form key→value dict for PATCH /api/settings/{category}."""
