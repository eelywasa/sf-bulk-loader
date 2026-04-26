"""Admin about endpoint — GET /api/admin/about.

Returns non-sensitive system information for admin users. Guarded by the
system.settings permission (same as all other Settings sub-pages). Only
registered when auth is required (i.e. not on the desktop profile).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import SYSTEM_SETTINGS, require_permission
from app.database import get_db
from app.models.user import User
from app.services.about import get_about_payload

router = APIRouter(prefix="/api/admin/about", tags=["admin-about"])

_require_settings = require_permission(SYSTEM_SETTINGS)


@router.get("")
async def get_about(
    _user: User = Depends(_require_settings),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await get_about_payload(session)
