"""Assembles the payload for GET /api/admin/about.

All values are safe to surface to a trusted admin — no secrets, no full DSNs,
no AWS credentials, no bucket names.
"""

from __future__ import annotations

import os
import sys
from importlib.metadata import version as _pkg_version
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as _settings
from app.database import engine
from app.models.input_connection import InputConnection


async def get_about_payload(session: AsyncSession) -> dict[str, Any]:
    return {
        "app": _app_info(),
        "distribution": _distribution_info(),
        "runtime": _runtime_info(),
        "database": await _database_info(session),
        "salesforce": await _salesforce_info(),
        "email": await _email_info(),
        "storage": await _storage_info(session),
    }


def _app_info() -> dict[str, Any]:
    return {
        "version": os.environ.get("APP_VERSION", "0.0.0-dev"),
        "git_sha": os.environ.get("APP_GIT_SHA", "unknown"),
        "build_time": os.environ.get("APP_BUILD_TIME", "unknown"),
    }


def _distribution_info() -> dict[str, Any]:
    return {
        "profile": _settings.app_distribution,
        "auth_mode": _settings.auth_mode,
    }


def _runtime_info() -> dict[str, Any]:
    vi = sys.version_info
    try:
        fastapi_ver = _pkg_version("fastapi")
    except Exception:
        fastapi_ver = "unknown"
    return {
        "python_version": f"{vi.major}.{vi.minor}.{vi.micro}",
        "fastapi_version": fastapi_ver,
    }


async def _database_info(session: AsyncSession) -> dict[str, Any]:
    backend = engine.dialect.name  # "sqlite" or "postgresql"
    # Wrap in a SAVEPOINT so a missing alembic_version table (e.g. fresh test
    # DB created via metadata.create_all rather than migrations) doesn't poison
    # the parent transaction on PostgreSQL — postgres aborts the whole tx on
    # any error unless the failure is contained in a nested transaction.
    try:
        async with session.begin_nested():
            result = await session.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            row = result.fetchone()
            alembic_head = row[0] if row else "unknown"
    except Exception:
        alembic_head = "unknown"
    return {
        "backend": backend,
        "alembic_head": alembic_head,
    }


async def _salesforce_info() -> dict[str, Any]:
    import app.services.settings.service as _svc

    try:
        api_version = await _svc.settings_service.get("sf_api_version")  # type: ignore[union-attr]
    except Exception:
        api_version = _settings.sf_api_version
    return {"api_version": api_version}


async def _email_info() -> dict[str, Any]:
    import app.services.settings.service as _svc

    try:
        backend = await _svc.settings_service.get("email_backend")  # type: ignore[union-attr]
    except Exception:
        backend = _settings.email_backend or "noop"
    backend = backend or "noop"
    return {
        "backend": backend,
        "enabled": backend != "noop",
    }


async def _storage_info(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        select(
            InputConnection.provider,
            InputConnection.direction,
            func.count().label("cnt"),
        ).group_by(InputConnection.provider, InputConnection.direction)
    )
    rows = result.all()

    input_counts: dict[str, int] = {}
    output_counts: dict[str, int] = {}

    for provider, direction, count in rows:
        if direction in ("in", "both"):
            input_counts[provider] = input_counts.get(provider, 0) + count
        if direction in ("out", "both"):
            output_counts[provider] = output_counts.get(provider, 0) + count

    # Local filesystem is always implicitly available on non-S3 profiles
    if _settings.input_storage_mode == "local" or _settings.app_distribution == "desktop":
        input_counts.setdefault("local", 1)

    return {
        "input_connections": input_counts,
        "output_connections": output_counts,
    }
