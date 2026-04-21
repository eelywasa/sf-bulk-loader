"""Me API — authenticated user self-service operations."""

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.observability.events import AuthEvent, OutcomeCode
from app.observability import metrics as obs_metrics
from app.observability import tracing
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
    with tracing.auth_password_change_span(user_id=str(current_user.id)) as span:
        # Step 1 — verify current password
        if current_user.hashed_password is None:
            # SAML-only account: run a dummy compare to avoid timing oracle, then reject
            verify_password(body.current_password, _DUMMY_HASH)
            outcome = OutcomeCode.NO_LOCAL_AUTH
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Password change rejected: no local auth",
                extra={
                    "event_name": AuthEvent.PASSWORD_CHANGED,
                    "outcome_code": outcome,
                    "user_id": str(current_user.id),
                },
            )
            obs_metrics.record_auth_password_change(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password change not available for this account",
            )

        if not verify_password(body.current_password, current_user.hashed_password):
            outcome = OutcomeCode.WRONG_CURRENT
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Password change rejected: wrong current password",
                extra={
                    "event_name": AuthEvent.PASSWORD_CHANGED,
                    "outcome_code": outcome,
                    "user_id": str(current_user.id),
                },
            )
            obs_metrics.record_auth_password_change(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid current password",
            )

        # Step 2 — validate new password strength
        try:
            validate_password_strength(body.new_password)
        except PasswordPolicyError as exc:
            outcome = OutcomeCode.POLICY_VIOLATION
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Password change rejected: policy violation",
                extra={
                    "event_name": AuthEvent.PASSWORD_CHANGED,
                    "outcome_code": outcome,
                    "user_id": str(current_user.id),
                },
            )
            obs_metrics.record_auth_password_change(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        # Step 3 — reject same password
        if body.new_password == body.current_password:
            outcome = OutcomeCode.SAME_PASSWORD
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Password change rejected: same password",
                extra={
                    "event_name": AuthEvent.PASSWORD_CHANGED,
                    "outcome_code": outcome,
                    "user_id": str(current_user.id),
                },
            )
            obs_metrics.record_auth_password_change(outcome)
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

        outcome = OutcomeCode.SUCCESS
        span.set_attribute("outcome", outcome)
        _log.info(
            "Password changed",
            extra={
                "event_name": AuthEvent.PASSWORD_CHANGED,
                "outcome_code": outcome,
                "user_id": str(user.id),
            },
        )
        obs_metrics.record_auth_password_change(outcome)

        # Step 5 — issue fresh JWT
        token = create_access_token(user)
        return TokenResponse(
            access_token=token,
            expires_in=settings.jwt_expiry_minutes * 60,
        )


# ── Login history ─────────────────────────────────────────────────────────────

_OUTCOME_MASK: dict[str, str] = {
    OutcomeCode.OK: "Success",
}
"""Map fine-grained outcome codes to the user-visible label.

Any outcome not present here is shown as ``"Failed"`` — this ensures we never
leak internal outcome code strings (e.g. ``tier1_auto``, ``wrong_password``)
to the end-user.  The only positive outcome is ``ok`` (written on successful
login) → ``"Success"``.
"""


class LoginHistoryEntry(BaseModel):
    attempted_at: datetime
    ip: str
    outcome: str  # "Success" or "Failed" — coarse mask


@router.get("/login-history", response_model=list[LoginHistoryEntry])
async def get_login_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[LoginHistoryEntry]:
    """Return recent sign-in activity for the authenticated user.

    - Only returns rows where ``user_id == current_user.id``
      (unknown-user rows with null user_id are excluded).
    - Outcome codes are masked to ``"Success"`` or ``"Failed"`` — fine-grained
      codes are for operator logs only.
    - Results ordered newest-first, bounded to ``limit`` (1–50, default 10).
    """
    rows = await db.execute(
        select(LoginAttempt)
        .where(LoginAttempt.user_id == current_user.id)
        .order_by(LoginAttempt.attempted_at.desc())
        .limit(limit)
    )
    attempts = rows.scalars().all()

    return [
        LoginHistoryEntry(
            attempted_at=row.attempted_at,
            ip=row.ip,
            outcome=_OUTCOME_MASK.get(row.outcome, "Failed"),
        )
        for row in attempts
    ]
