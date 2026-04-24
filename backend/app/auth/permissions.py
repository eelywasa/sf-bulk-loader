"""Permission key vocabulary and enforcement dependency for the profile-based RBAC model (spec §5.1).

Keys are plain module-level strings — simpler than an Enum for DB storage
and JSON serialisation. ALL_PERMISSION_KEYS is the authoritative set used by
the startup check in main.py to catch typos in seed data or hand-edits.

require_permission() is the FastAPI dependency factory for per-route enforcement (SFBL-195).
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from fastapi import Depends, HTTPException, status

from app.models.user import User
from app.services.auth import get_current_user

_log = logging.getLogger(__name__)

# Connection permissions
CONNECTIONS_VIEW = "connections.view"
CONNECTIONS_VIEW_CREDENTIALS = "connections.view_credentials"
CONNECTIONS_MANAGE = "connections.manage"

# Plan permissions
PLANS_VIEW = "plans.view"
PLANS_MANAGE = "plans.manage"

# Run permissions
RUNS_VIEW = "runs.view"
RUNS_EXECUTE = "runs.execute"
RUNS_ABORT = "runs.abort"

# File permissions
FILES_VIEW = "files.view"
FILES_VIEW_CONTENTS = "files.view_contents"

# Admin permissions
USERS_MANAGE = "users.manage"
USERS_RESET_2FA = "admin.users.reset_2fa"
SYSTEM_SETTINGS = "system.settings"

ALL_PERMISSION_KEYS: frozenset[str] = frozenset(
    {
        CONNECTIONS_VIEW,
        CONNECTIONS_VIEW_CREDENTIALS,
        CONNECTIONS_MANAGE,
        PLANS_VIEW,
        PLANS_MANAGE,
        RUNS_VIEW,
        RUNS_EXECUTE,
        RUNS_ABORT,
        FILES_VIEW,
        FILES_VIEW_CONTENTS,
        USERS_MANAGE,
        USERS_RESET_2FA,
        SYSTEM_SETTINGS,
    }
)


def require_permission(key: str) -> Callable[..., Awaitable[User]]:
    """FastAPI dependency factory — enforces a single permission key.

    Usage::

        @router.get("/foo")
        async def foo(user: User = Depends(require_permission("plans.view"))):
            ...

    Behaviour:
    1. Validates *key* against ALL_PERMISSION_KEYS at factory-call time (fail-fast on typo).
    2. Calls ``get_current_user`` (existing auth gate).
    3. In desktop mode (``auth_mode='none'``), the virtual desktop user has all keys — no-op gate.
    4. In hosted mode, raises 403 if ``key`` is not in ``current_user.profile.permission_keys``.
    5. On denial, emits a WARN log with ``event_name="auth.permission_denied"``.

    Returns the authenticated User on success.
    """
    if key not in ALL_PERMISSION_KEYS:
        raise ValueError(
            f"require_permission({key!r}) called with an unknown permission key. "
            f"Valid keys: {sorted(ALL_PERMISSION_KEYS)}"
        )

    async def _dependency(current_user: User = Depends(get_current_user)) -> User:
        from app.config import settings as _settings

        # Desktop mode — virtual desktop user always passes (no profile assigned)
        if _settings.auth_mode == "none":
            return current_user

        # Hosted mode — check profile permission_keys
        profile = current_user.profile
        if profile is None or key not in profile.permission_keys:
            profile_name = profile.name if profile is not None else "none"
            from app.observability.events import AuthEvent, OutcomeCode

            _log.warning(
                "Permission denied",
                extra={
                    "event_name": AuthEvent.PERMISSION_DENIED,
                    "outcome_code": OutcomeCode.PERMISSION_DENIED,
                    "required_permission": key,
                    "user_id": str(current_user.id),
                    "profile": profile_name,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "permission_denied",
                    "required_permission": key,
                },
            )

        return current_user

    return _dependency
