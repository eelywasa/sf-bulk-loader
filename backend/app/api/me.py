"""Me API — authenticated user self-service operations."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.auth import PasswordChangeRequest, TokenResponse
from app.services.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    validate_password_strength,
    verify_password,
    PasswordPolicyError,
)

router = APIRouter(prefix="/api/me", tags=["me"])

_log = logging.getLogger(__name__)

# Dummy hash used for constant-time comparisons when a user has no local password
_DUMMY_HASH = hash_password("__dummy_constant_time_hash__")


@router.post("/password", response_model=TokenResponse)
async def change_password(
    body: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Change the authenticated user's password.

    Steps:
    1. Verify current password (constant-time; dummy compare for SAML-only accounts).
    2. Validate new password strength.
    3. Reject if new == current.
    4. Hash and persist new password; bump password_changed_at watermark.
    5. Issue and return a fresh JWT.
    """
    # Step 1 — verify current password
    if current_user.hashed_password is None:
        # SAML-only account: run a dummy compare to avoid timing oracle, then reject
        verify_password(body.current_password, _DUMMY_HASH)
        _log.warning(
            "Password change rejected: no local auth",
            extra={
                "event_name": "auth.password.change_failed",
                "outcome_code": "no_local_auth",
                "user_id": str(current_user.id),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password change not available for this account",
        )

    if not verify_password(body.current_password, current_user.hashed_password):
        _log.warning(
            "Password change rejected: wrong current password",
            extra={
                "event_name": "auth.password.change_failed",
                "outcome_code": "wrong_current",
                "user_id": str(current_user.id),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid current password",
        )

    # Step 2 — validate new password strength
    try:
        validate_password_strength(body.new_password)
    except PasswordPolicyError as exc:
        _log.warning(
            "Password change rejected: policy violation",
            extra={
                "event_name": "auth.password.change_failed",
                "outcome_code": "policy_violation",
                "user_id": str(current_user.id),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # Step 3 — reject same password
    if body.new_password == body.current_password:
        _log.warning(
            "Password change rejected: same password",
            extra={
                "event_name": "auth.password.change_failed",
                "outcome_code": "same_password",
                "user_id": str(current_user.id),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ",
        )

    # Step 4 — re-fetch user in this session, update and commit
    user = await db.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    user.hashed_password = hash_password(body.new_password)
    # Truncate to whole seconds so the watermark aligns with JWT's integer ``iat``.
    # Sub-second precision would cause the freshly-issued token (same second) to
    # be rejected by the strict-less-than check in get_current_user.
    now_utc = datetime.now(timezone.utc)
    user.password_changed_at = now_utc.replace(microsecond=0)
    await db.commit()
    await db.refresh(user)

    _log.info(
        "password changed",
        extra={
            "event_name": "auth.password.changed",
            "user_id": str(user.id),
        },
    )

    # Step 5 — issue fresh JWT
    token = create_access_token(user)
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expiry_minutes * 60,
    )
