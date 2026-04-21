"""Admin users API — account management endpoints (SFBL-191).

POST /api/admin/users/{id}/unlock

Requires admin auth (hosted profiles only). Clears a user's tier-1 or tier-2
lock and optionally transitions status from 'locked' back to 'active'.

Emits:
- ``auth.account.unlocked`` log event with ``outcome_code=admin_manual``
- A ``login_attempt`` audit row with ``outcome=admin_unlock``
- ``auth_account_unlocks_total{method=admin_manual}`` metric counter
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.observability.events import AuthEvent, OutcomeCode
from app.observability.metrics import record_account_unlocked
from app.schemas.auth import UserResponse
from app.services.auth import get_current_user

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])

_log = logging.getLogger(__name__)


# ── Admin dependency ─────────────────────────────────────────────────────────


def require_admin(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """Dependency that requires the current user to have the 'admin' role."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return current_user


# ── Unlock endpoint ───────────────────────────────────────────────────────────


@router.post(
    "/{user_id}/unlock",
    response_model=UserResponse,
    summary="Unlock an account (admin only)",
)
async def admin_unlock_user(
    user_id: str,
    admin: Annotated[User, Depends(require_admin)],
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Clear tier-1 and/or tier-2 lockout for the target user.

    - Clears ``locked_until`` (tier-1 auto-lock).
    - Resets ``failed_login_count`` to 0.
    - If ``status == 'locked'``, transitions it back to ``'active'``.
    - Refuses to act on the caller's own user id (400) to prevent
      self-unlock ambiguity.
    - Persists an audit ``login_attempt`` row with ``outcome=admin_unlock``.
    - Emits ``auth.account.unlocked`` with ``outcome_code=admin_manual``.
    """
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot unlock your own account",
        )

    target: User | None = await db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    now = datetime.now(timezone.utc)

    # Apply unlock
    target.locked_until = None
    target.failed_login_count = 0
    if target.status == "locked":
        target.status = "active"

    # Audit row
    audit_row = LoginAttempt(
        id=str(uuid.uuid4()),
        user_id=target.id,
        username=target.username or "",
        ip="admin",
        user_agent=None,
        outcome=OutcomeCode.ADMIN_UNLOCK,
        attempted_at=now,
    )
    db.add(audit_row)

    await db.commit()
    await db.refresh(target)

    _log.info(
        "Account unlocked by admin",
        extra={
            "event_name": AuthEvent.ACCOUNT_UNLOCKED,
            "outcome_code": OutcomeCode.ADMIN_MANUAL,
            "user_id": target.id,
            "admin_user_id": admin.id,
        },
    )
    record_account_unlocked(OutcomeCode.ADMIN_MANUAL)

    return UserResponse.model_validate(target)
