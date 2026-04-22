"""Public invitation-accept API (SFBL-202).

These endpoints are UNAUTHENTICATED — the raw token is the credential.

Endpoints
---------
GET  /api/invitations/{raw_token}         — validate token, return user info for welcome screen
POST /api/invitations/{raw_token}/accept  — set password, activate user, return JWT
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.invitation_token import InvitationToken
from app.models.user import User
from app.observability.events import AuthEvent, OutcomeCode
from app.schemas.invitations import (
    InvitationAcceptRequest,
    InvitationAcceptResponse,
    InvitationInfoResponse,
)
from app.services.auth import (
    PasswordPolicyError,
    create_access_token,
    hash_password,
    validate_password_strength,
)

router = APIRouter(prefix="/api/invitations", tags=["invitations"])

_log = logging.getLogger(__name__)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


async def _lookup_pending_token(
    raw_token: str,
    db: AsyncSession,
) -> InvitationToken:
    """Look up a pending (unused, unexpired) InvitationToken by raw token.

    Raises 404 if the token is not found, used, or expired.
    """
    token_hash = _hash_token(raw_token)
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(InvitationToken).where(
            InvitationToken.token_hash == token_hash,
        )
    )
    inv_token: InvitationToken | None = result.scalar_one_or_none()

    if inv_token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "invalid_token", "message": "Invitation link is invalid or has expired."},
        )

    # Ensure expires_at is timezone-aware for comparison
    expires_at = inv_token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if inv_token.used_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"error": "used_token", "message": "This invitation has already been accepted."},
        )

    if expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "expired_token", "message": "Invitation link is invalid or has expired."},
        )

    return inv_token


# ── GET /api/invitations/{raw_token} ─────────────────────────────────────────


@router.get(
    "/{raw_token}",
    response_model=InvitationInfoResponse,
    summary="Validate invitation token and return user info",
)
@router.get(
    "/{raw_token}/",
    response_model=InvitationInfoResponse,
    include_in_schema=False,
)
async def get_invitation_info(
    raw_token: str,
    db: AsyncSession = Depends(get_db),
) -> InvitationInfoResponse:
    """Validate the invitation token and return enough info to render the welcome screen.

    Returns 404 if the token is invalid, used, or expired.
    """
    inv_token = await _lookup_pending_token(raw_token, db)

    if inv_token.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "invalid_token", "message": "Invitation link is invalid or has expired."},
        )

    user: User | None = await db.get(User, inv_token.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "invalid_token", "message": "Invitation link is invalid or has expired."},
        )

    profile_name: str | None = None
    if user.profile is not None:
        profile_name = user.profile.name

    return InvitationInfoResponse(
        email=user.email,
        display_name=user.display_name,
        profile_name=profile_name,
    )


# ── POST /api/invitations/{raw_token}/accept ─────────────────────────────────


@router.post(
    "/{raw_token}/accept",
    response_model=InvitationAcceptResponse,
    summary="Accept invitation: set password and activate account",
)
@router.post(
    "/{raw_token}/accept/",
    response_model=InvitationAcceptResponse,
    include_in_schema=False,
)
async def accept_invitation(
    raw_token: str,
    body: InvitationAcceptRequest,
    db: AsyncSession = Depends(get_db),
) -> InvitationAcceptResponse:
    """Accept an invitation token by setting a password.

    - Validates password strength (raises 422 on policy failure).
    - Atomically redeems the token (single UPDATE with WHERE guard to handle races).
    - Sets user.hashed_password, status='active', password_changed_at, last_login_at.
    - Returns a JWT so the user is immediately logged in.

    Returns 404 if the token is invalid/expired, 410 if the token was already used.
    """
    # Validate password strength BEFORE hitting the DB
    try:
        validate_password_strength(body.password)
    except PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "password_policy_violation",
                "failures": exc.failures,
            },
        )

    token_hash = _hash_token(raw_token)
    now = datetime.now(timezone.utc)

    # Atomic redeem: UPDATE with all conditions in WHERE — only one concurrent
    # request can win this race; the second gets rowcount=0 and is rejected.
    redeem_result = await db.execute(
        update(InvitationToken)
        .where(
            InvitationToken.token_hash == token_hash,
            InvitationToken.used_at.is_(None),
            InvitationToken.expires_at > sa.func.now(),
        )
        .values(used_at=sa.func.now())
        .returning(InvitationToken.id, InvitationToken.user_id)
    )
    redeemed_row = redeem_result.first()

    if redeemed_row is None:
        # Either token not found, already used, or expired — don't distinguish
        # between used and expired to avoid token enumeration
        _log.warning(
            "Invitation accept failed: token not found, used, or expired",
            extra={
                "event_name": AuthEvent.INVITATION_ACCEPTED,
                "outcome_code": OutcomeCode.INVALID_TOKEN,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"error": "invitation_unavailable", "message": "This invitation is no longer valid."},
        )

    user_id: str = redeemed_row.user_id

    if user_id is None:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "invalid_token", "message": "Invitation link is invalid."},
        )

    user: User | None = await db.get(User, user_id)
    if user is None:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "invalid_token", "message": "Invitation link is invalid."},
        )

    # Activate the user
    user.hashed_password = hash_password(body.password)
    user.status = "active"
    user.password_changed_at = now
    user.last_login_at = now

    await db.commit()
    await db.refresh(user)

    access_token = create_access_token(user)

    _log.info(
        "Invitation accepted",
        extra={
            "event_name": AuthEvent.INVITATION_ACCEPTED,
            "outcome_code": OutcomeCode.INVITATION_ACCEPTED,
            "user_id": user.id,
        },
    )

    return InvitationAcceptResponse(access_token=access_token)
