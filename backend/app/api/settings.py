"""Settings API — settings CRUD for DB-backed runtime settings (SFBL-154).

Endpoints
---------
GET  /api/settings                  — all categories (secrets masked)
GET  /api/settings/{category}       — single category (secrets masked, 404 if unknown)
PATCH /api/settings/{category}      — update keys within a category

All endpoints require ``system.settings`` permission (SFBL-195).
Previously guarded by ``require_admin``; refactored to the permission model so that
any profile with ``system.settings`` can manage settings, not only is_admin accounts.

Cache propagation note (PATCH)
-------------------------------
SettingsService caches values for 60 seconds per key.  A successful PATCH
invalidates the cache entry on the instance that handled the request, but in
a multi-process deployment other workers will see the old value for up to 60
seconds.  Clients are informed of this via the ``X-Settings-Cache-TTL: 60``
response header, which is present on both GET and PATCH responses.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.app_setting import AppSetting
from app.models.user import User
from app.schemas.settings import AllSettings, CategorySettings, PatchRequest, SettingValue
from app.auth.permissions import require_permission, SYSTEM_SETTINGS
from app.observability.events import MfaEvent, OutcomeCode
from app.observability.metrics import set_mfa_tenant_required
from app.services.settings.registry import SETTINGS_REGISTRY
import app.services.settings.service as _settings_svc_module

_require_settings = require_permission(SYSTEM_SETTINGS)

_log = logging.getLogger(__name__)

_CACHE_TTL_HEADER = "X-Settings-Cache-TTL"
_CACHE_TTL_VALUE = "60"

# Must match the dispatch table in build_email_service() — persisting any other
# value silently breaks startup on the next process restart.
_EMAIL_BACKEND_ALLOWED = {"noop", "smtp", "ses"}

router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(_require_settings)],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask(value: Any, is_secret: bool) -> Any:
    """Replace a secret value with '***' if non-None, else leave as-is."""
    if is_secret and value is not None:
        return "***"
    return value


async def _build_category_settings(
    category: str,
    db: AsyncSession,
) -> CategorySettings:
    """Return a CategorySettings for *category*, secrets masked."""
    keys = [k for k, m in SETTINGS_REGISTRY.items() if m.category == category]

    # Bulk-fetch DB rows for this category so we can get updated_at
    rows: dict[str, AppSetting] = {}
    result = await db.execute(
        select(AppSetting).where(AppSetting.category == category)
    )
    for row in result.scalars():
        rows[row.key] = row

    sv_list: list[SettingValue] = []
    for key in keys:
        meta = SETTINGS_REGISTRY[key]
        # Get the typed value via service (handles decryption + coercion + cache)
        raw_value = await _settings_svc_module.settings_service.get(key)  # type: ignore[union-attr]
        db_row = rows.get(key)
        updated_at: datetime | None = None
        if db_row is not None:
            updated_at = db_row.updated_at
            if updated_at is not None and updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)

        sv_list.append(
            SettingValue(
                key=key,
                value=_mask(raw_value, meta.is_secret),
                type=meta.type,
                is_secret=meta.is_secret,
                description=meta.description,
                restart_required=meta.restart_required,
                updated_at=updated_at,
            )
        )

    return CategorySettings(category=category, settings=sv_list)


def _known_categories() -> list[str]:
    """Return sorted list of unique categories in the registry."""
    seen: list[str] = []
    for meta in SETTINGS_REGISTRY.values():
        if meta.category not in seen:
            seen.append(meta.category)
    return seen


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=AllSettings, include_in_schema=False)
@router.get("/", response_model=AllSettings)
async def get_all_settings(
    response: Response,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_settings),
) -> AllSettings:
    """Return all settings grouped by category. Secrets are masked as '***'."""
    categories = _known_categories()
    cat_list = [await _build_category_settings(cat, db) for cat in categories]
    response.headers[_CACHE_TTL_HEADER] = _CACHE_TTL_VALUE
    return AllSettings(categories=cat_list)


@router.get("/{category}", response_model=CategorySettings)
async def get_category_settings(
    category: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_settings),
) -> CategorySettings:
    """Return settings for a single category. 404 if the category is unknown."""
    if category not in _known_categories():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown settings category: {category!r}",
        )
    cat_settings = await _build_category_settings(category, db)
    response.headers[_CACHE_TTL_HEADER] = _CACHE_TTL_VALUE
    return cat_settings


@router.patch("/{category}", response_model=CategorySettings)
async def patch_category_settings(
    category: str,
    body: PatchRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(_require_settings),
) -> CategorySettings:
    """Update one or more settings within *category*.

    Validation rules (all-or-nothing — no writes occur if any fail):

    - Every key must exist in the registry → 422 with ``{"field": key, "error": "unknown"}``
    - Every key must belong to ``category`` (no cross-category writes) → 422
    - Values must be type-coercible per the registry → 422
    - Secret keys: empty-string value means "keep existing" (no write for that key)

    In a multi-process deployment other workers may see stale values for up to
    60 seconds (see ``X-Settings-Cache-TTL`` header).
    """
    updates: dict[str, Any] = body.root

    if not updates:
        # Nothing to do — return current state
        response.headers[_CACHE_TTL_HEADER] = _CACHE_TTL_VALUE
        return await _build_category_settings(category, db)

    # -- Validation pass (collect all errors before writing) ------------------
    errors: list[dict[str, str]] = []
    coerced: dict[str, Any] = {}
    skip_keys: set[str] = set()

    for key, value in updates.items():
        # 1. Key must be in registry
        if key not in SETTINGS_REGISTRY:
            errors.append({"field": key, "error": "unknown"})
            continue

        meta = SETTINGS_REGISTRY[key]

        # 2. Key must belong to the requested category
        if meta.category != category:
            errors.append({"field": key, "error": f"belongs to category '{meta.category}', not '{category}'"})
            continue

        # 3. Secret field with empty-string value → skip (keep existing)
        if meta.is_secret and value == "":
            skip_keys.add(key)
            continue

        # 4. Type coercion
        try:
            from app.services.settings.service import _coerce  # noqa: PLC0415
            coerced_value = _coerce(value, meta.type)
        except (ValueError, TypeError):
            errors.append({"field": key, "error": f"cannot coerce value to type '{meta.type}'"})
            continue

        # 5. Enum-style allow-list for known-constrained keys.  Kept inline here
        # rather than adding an `allowed_values` field to SettingMeta since this
        # is the only key (today) with a hard enum constraint at write time.
        # Accepting arbitrary strings silently persists them, which then breaks
        # init_email_service_async() on next restart because build_email_service
        # raises for unknown backends.
        if key == "email_backend" and coerced_value not in _EMAIL_BACKEND_ALLOWED:
            errors.append({
                "field": key,
                "error": f"must be one of {sorted(_EMAIL_BACKEND_ALLOWED)}",
            })
            continue

        coerced[key] = coerced_value

    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=errors,
        )

    # -- Write pass -----------------------------------------------------------
    for key, value in coerced.items():
        prior: Any = None
        if key == "require_2fa":
            try:
                prior = await _settings_svc_module.settings_service.get(key)  # type: ignore[union-attr]
            except Exception:  # pragma: no cover - defensive
                prior = None
        await _settings_svc_module.settings_service.set(key, value)  # type: ignore[union-attr]

        # 2FA tenant toggle side-effects (SFBL-252): update the Prometheus
        # gauge and emit the canonical mfa.tenant_toggle.changed event so
        # dashboards and audit trails pick the change up.
        if key == "require_2fa":
            enabled = bool(value)
            set_mfa_tenant_required(enabled)
            if bool(prior) != enabled:
                _log.info(
                    "require_2fa tenant toggle changed",
                    extra={
                        "event_name": MfaEvent.TENANT_TOGGLE_CHANGED,
                        "outcome_code": OutcomeCode.OK,
                        "require_2fa": enabled,
                        "prior_value": bool(prior),
                    },
                )

    response.headers[_CACHE_TTL_HEADER] = _CACHE_TTL_VALUE
    return await _build_category_settings(category, db)
