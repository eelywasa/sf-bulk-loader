"""Helpers for resolving effective input/output directories (SFBL-257).

On the desktop profile the user may configure custom paths via the DB-backed
settings (``desktop_input_dir``, ``desktop_output_dir``).  The settings service
caches values for 60 s, so a change in the UI is reflected within a minute
without any process restart.

On hosted profiles (self_hosted, aws_hosted) we fall back to the config
singleton values, which are populated from env vars at startup.
"""

from __future__ import annotations

from app.config import settings as _config


async def effective_input_dir() -> str:
    """Return the current input directory for the active distribution profile."""
    if _config.app_distribution == "desktop":
        from app.services.settings.service import settings_service  # noqa: PLC0415

        if settings_service is not None:
            val: str = await settings_service.get("desktop_input_dir")
            if val:
                return val
    return _config.input_dir


async def effective_output_dir() -> str:
    """Return the current output directory for the active distribution profile."""
    if _config.app_distribution == "desktop":
        from app.services.settings.service import settings_service  # noqa: PLC0415

        if settings_service is not None:
            val: str = await settings_service.get("desktop_output_dir")
            if val:
                return val
    return _config.output_dir
