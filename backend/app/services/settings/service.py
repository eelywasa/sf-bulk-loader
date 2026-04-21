"""SettingsService — async singleton for DB-backed application settings (SFBL-153).

Resolution order for get():
  1. DB row (decrypted if is_secret)
  2. Registry default

set() validates type, encrypts secrets, upserts the DB row, and invalidates
the in-memory cache entry.

seed_from_env() is called once at startup: for each registered setting that
has an env_var, if no DB row exists, the env value (or registry default if
the env var is unset) is written to the DB.  Subsequent calls are idempotent
— existing rows are never overwritten.

Cache: per-key (value, expires_at) tuple with a 60-second TTL.  Cache entries
are invalidated immediately on set().
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.app_setting import AppSetting
from app.services.settings.registry import SETTINGS_REGISTRY
from app.utils.encryption import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS: float = 60.0


def _coerce(value: Any, type_str: str) -> Any:
    """Coerce *value* to the Python type named by *type_str*."""
    if type_str == "int":
        return int(value)
    if type_str == "float":
        return float(value)
    if type_str == "bool":
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("1", "true", "yes", "on")
    # "str"
    return str(value)


class SettingsService:
    """Async singleton that reads/writes settings from the app_settings table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        # key → (coerced_value, expires_at_monotonic)
        self._cache: dict[str, tuple[Any, float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Any:
        """Return the typed value for *key*.

        Resolution order: DB row → registry default.
        Results are cached for _CACHE_TTL_SECONDS.

        Raises KeyError if the key is not in the registry.
        """
        meta = self._require_meta(key)

        # Check cache
        cached = self._cache.get(key)
        if cached is not None:
            value, expires_at = cached
            if time.monotonic() < expires_at:
                return value

        # Read from DB
        async with self._session_factory() as session:
            row = await session.get(AppSetting, key)

        if row is not None and row.value is not None:
            raw = decrypt_secret(row.value) if row.is_encrypted else row.value
            coerced = _coerce(raw, meta.type)
        else:
            coerced = _coerce(meta.default, meta.type)

        self._cache[key] = (coerced, time.monotonic() + _CACHE_TTL_SECONDS)
        return coerced

    async def set(self, key: str, value: Any) -> None:
        """Validate, optionally encrypt, then upsert *value* for *key*.

        Invalidates the cache entry immediately.

        Raises KeyError if the key is not in the registry.
        Raises TypeError if the value cannot be coerced to the registered type.
        """
        meta = self._require_meta(key)

        # Validate / coerce
        try:
            coerced = _coerce(value, meta.type)
        except (ValueError, TypeError) as exc:
            raise TypeError(
                f"Cannot coerce {value!r} to type {meta.type!r} for setting {key!r}"
            ) from exc

        str_value = str(coerced)
        is_encrypted = meta.is_secret
        if is_encrypted:
            str_value = encrypt_secret(str_value)

        async with self._session_factory() as session:
            row = await session.get(AppSetting, key)
            now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
            if row is None:
                row = AppSetting(
                    key=key,
                    value=str_value,
                    is_encrypted=is_encrypted,
                    category=meta.category,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.value = str_value
                row.is_encrypted = is_encrypted
                row.updated_at = now
            await session.commit()

        # Invalidate cache
        self._cache.pop(key, None)

    async def get_category(self, category: str) -> dict[str, Any]:
        """Return all settings in *category*, with secrets decrypted."""
        keys = [k for k, m in SETTINGS_REGISTRY.items() if m.category == category]
        return {k: await self.get(k) for k in keys}

    async def seed_from_env(self) -> None:
        """Idempotent startup seed.

        For each registry entry that has an env_var:
        - If a DB row already exists → skip (never overwrite).
        - Else if the env var is set and non-empty → use that value.
        - Else → use the registry default.
        Secrets are encrypted before writing.
        """
        async with self._session_factory() as session:
            for key, meta in SETTINGS_REGISTRY.items():
                # Skip if row already exists
                existing = await session.get(AppSetting, key)
                if existing is not None:
                    continue

                # Determine value: env var beats registry default
                if meta.env_var:
                    raw_env = os.environ.get(meta.env_var, "")
                    raw = raw_env if raw_env else str(meta.default)
                else:
                    raw = str(meta.default)

                is_encrypted = meta.is_secret
                stored = encrypt_secret(raw) if is_encrypted else raw

                now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
                row = AppSetting(
                    key=key,
                    value=stored,
                    is_encrypted=is_encrypted,
                    category=meta.category,
                    updated_at=now,
                )
                session.add(row)

            await session.commit()

        logger.info("settings seed_from_env completed", extra={"seeded_keys": list(SETTINGS_REGISTRY.keys())})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_meta(key: str):  # noqa: ANN205
        """Return the SettingMeta for *key* or raise KeyError."""
        try:
            return SETTINGS_REGISTRY[key]
        except KeyError:
            raise KeyError(f"Unknown setting key: {key!r}") from None


# ---------------------------------------------------------------------------
# Module-level singleton — wired up in main.py lifespan via init_settings_service
# ---------------------------------------------------------------------------

settings_service: SettingsService | None = None


def init_settings_service(session_factory: async_sessionmaker[AsyncSession]) -> SettingsService:
    """Initialise the module-level singleton.  Call once from FastAPI lifespan."""
    global settings_service
    settings_service = SettingsService(session_factory)
    return settings_service


def get_settings_service() -> SettingsService:
    """Return the module-level singleton.

    Raises RuntimeError if called before init_settings_service().
    """
    if settings_service is None:
        raise RuntimeError(
            "SettingsService has not been initialised. "
            "Call init_settings_service() in the app lifespan before using this function."
        )
    return settings_service
