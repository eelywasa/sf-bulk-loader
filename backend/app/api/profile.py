"""Profile API — SFBL-148: profile update + email-change verification.

Endpoints:
  PUT  /api/me/profile          — update display_name
  POST /api/me/email-change/request  — request an email address change
  POST /api/me/email-change/confirm  — confirm via token (public)
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.email_change_token import EmailChangeToken
from app.models.user import User
from app.observability.events import AuthEvent, OutcomeCode
from app.observability import metrics as obs_metrics
from app.observability import tracing
from app.schemas.auth import (
    EmailChangeConfirm,
    EmailChangeRequest,
    ProfileUpdateRequest,
    UserResponse,
)
from app.services.auth import get_current_user
from app.services.email.message import EmailCategory
from app.services.email.service import get_email_service
from app.services.rate_limit import check_and_record

router = APIRouter(prefix="/api/me", tags=["profile"])

_log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _mask_email(email: str) -> str:
    """Mask the local part of an email address.

    Shows first and last character of local part only:
      john.doe@example.com → j*****e@example.com
    Single-character local parts are fully masked:
      a@example.com → *@example.com
    """
    local, _, domain = email.partition("@")
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}@{domain}"


# ── PUT /api/me/profile ───────────────────────────────────────────────────────


@router.put("/profile", response_model=UserResponse)
async def update_profile(
    body: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Update the authenticated user's display name.

    Validates that the trimmed display_name is 1-120 characters if provided.
    Returns a fresh UserResponse reflecting the updated values.
    """
    if body.display_name is not None:
        trimmed = body.display_name.strip()
        if not trimmed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="display_name must not be empty or whitespace-only",
            )
        if len(trimmed) > 120:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="display_name must be 120 characters or fewer",
            )
        body = ProfileUpdateRequest(display_name=trimmed)

    # Re-fetch user in this session to get a managed ORM instance
    user = await db.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    if body.display_name is not None:
        user.display_name = body.display_name

    await db.commit()
    await db.refresh(user)

    _log.info(
        "Profile updated",
        extra={
            "event_name": AuthEvent.PROFILE_UPDATED,
            "outcome_code": OutcomeCode.OK,
            "user_id": user.id,
        },
    )
    return UserResponse.model_validate(user)


# ── POST /api/me/email-change/request ────────────────────────────────────────


@router.post("/email-change/request", status_code=status.HTTP_202_ACCEPTED)
async def request_email_change(
    body: EmailChangeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    email_service=Depends(get_email_service),
) -> dict:
    """Request an email address change.

    Sends a verification link to the new address and a notice to the current
    address.  Rate-limited by user_id.

    Note: the "email already in use" 400 response technically leaks that the
    email is in use to the authenticated user.  This is an accepted enumeration
    tradeoff per SFBL-148 spec — the endpoint is authenticated so the caller
    is already known.
    """
    with tracing.auth_email_change_request_span(user_id=str(current_user.id)) as span:
        new_email = str(body.new_email).lower()

        # Per-user rate limit
        rate_key = f"rl:email_change:user:{current_user.id}"
        allowed = await check_and_record(
            rate_key,
            limit=settings.email_change_rate_limit_per_user_hour,
            window_seconds=3600,
        )
        if not allowed:
            outcome = OutcomeCode.RATE_LIMITED
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Email change rate-limited",
                extra={
                    "event_name": AuthEvent.EMAIL_CHANGE_REQUESTED,
                    "outcome_code": outcome,
                    "user_id": current_user.id,
                },
            )
            obs_metrics.record_auth_email_change_request(outcome)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many email change requests",
            )

        # Reject unchanged email
        if current_user.email and current_user.email.lower() == new_email:
            outcome = OutcomeCode.EMAIL_UNCHANGED
            span.set_attribute("outcome", outcome)
            _log.info(
                "Email change rejected: email unchanged",
                extra={
                    "event_name": AuthEvent.EMAIL_CHANGE_REQUESTED,
                    "outcome_code": outcome,
                    "user_id": current_user.id,
                },
            )
            obs_metrics.record_auth_email_change_request(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email unchanged",
            )

        # Check if email is already in use by another active user (case-insensitive)
        existing_result = await db.execute(
            select(User).where(
                User.email.ilike(new_email),
                User.is_active.is_(True),
                User.id != current_user.id,
            )
        )
        if existing_result.scalar_one_or_none() is not None:
            outcome = OutcomeCode.EMAIL_IN_USE
            span.set_attribute("outcome", outcome)
            _log.info(
                "Email change rejected: email already in use",
                extra={
                    "event_name": AuthEvent.EMAIL_CHANGE_REQUESTED,
                    "outcome_code": outcome,
                    "user_id": current_user.id,
                },
            )
            obs_metrics.record_auth_email_change_request(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use",
            )

        # Generate token and store hash
        raw_token = secrets.token_hex(32)
        token_hash = _sha256_hex(raw_token)
        now_utc = datetime.now(timezone.utc)
        expires_at = now_utc + timedelta(minutes=settings.email_change_ttl_minutes)

        # Invalidate all prior unused email-change tokens for this user
        prior_tokens_result = await db.execute(
            select(EmailChangeToken).where(
                EmailChangeToken.user_id == current_user.id,
                EmailChangeToken.used_at.is_(None),
            )
        )
        for prior_token in prior_tokens_result.scalars().all():
            prior_token.used_at = now_utc

        # Create the new token
        token_record = EmailChangeToken(
            user_id=current_user.id,
            token_hash=token_hash,
            new_email=new_email,
            expires_at=expires_at,
            created_at=now_utc,
        )
        db.add(token_record)
        await db.commit()
        await db.refresh(token_record)

        token_id = token_record.id
        confirm_url = f"{settings.frontend_base_url}/verify-email/{raw_token}"
        display_name = current_user.display_name or current_user.username or "User"

        # Send verification email to new_email
        await email_service.send_template(
            "auth/email_change_verify",
            {
                "user_display_name": display_name,
                "confirm_url": confirm_url,
                "new_email": new_email,
                "expires_in_minutes": settings.email_change_ttl_minutes,
            },
            to=new_email,
            category=EmailCategory.AUTH,
            idempotency_key=f"email-change-verify:{token_id}",
        )

        # Send notice email to current address (if known)
        if current_user.email:
            new_email_masked = _mask_email(new_email)
            await email_service.send_template(
                "auth/email_change_notice",
                {
                    "user_display_name": display_name,
                    "new_email_masked": new_email_masked,
                },
                to=current_user.email,
                category=EmailCategory.AUTH,
                idempotency_key=f"email-change-notice:{token_id}",
            )

        outcome = OutcomeCode.SENT
        span.set_attribute("outcome", outcome)
        _log.info(
            "Email change requested",
            extra={
                "event_name": AuthEvent.EMAIL_CHANGE_REQUESTED,
                "outcome_code": outcome,
                "user_id": current_user.id,
                "token_id": token_id,
            },
        )
        obs_metrics.record_auth_email_change_request(outcome)
        return {}


# ── POST /api/me/email-change/confirm ────────────────────────────────────────


@router.post("/email-change/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_email_change(
    body: EmailChangeConfirm,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Confirm an email address change using the token from the verification link.

    Public endpoint — the token supplies identity.  Does NOT bump
    password_changed_at, so existing JWTs remain valid after an email change.
    """
    with tracing.auth_email_change_confirm_span() as span:
        token_hash = _sha256_hex(body.token)
        now_utc = datetime.now(timezone.utc)

        # Look up token by hash
        result = await db.execute(
            select(EmailChangeToken).where(EmailChangeToken.token_hash == token_hash)
        )
        token_record = result.scalar_one_or_none()

        if token_record is None:
            outcome = OutcomeCode.INVALID_TOKEN
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Email change confirm: invalid token",
                extra={
                    "event_name": AuthEvent.EMAIL_CHANGE_CONFIRMED,
                    "outcome_code": outcome,
                },
            )
            obs_metrics.record_auth_email_change_confirm(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired token",
            )

        if token_record.used_at is not None:
            outcome = OutcomeCode.USED_TOKEN
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Email change confirm: token already used",
                extra={
                    "event_name": AuthEvent.EMAIL_CHANGE_CONFIRMED,
                    "outcome_code": outcome,
                    "token_id": token_record.id,
                },
            )
            obs_metrics.record_auth_email_change_confirm(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired token",
            )

        expires_at = token_record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now_utc:
            outcome = OutcomeCode.EXPIRED_TOKEN
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Email change confirm: token expired",
                extra={
                    "event_name": AuthEvent.EMAIL_CHANGE_CONFIRMED,
                    "outcome_code": outcome,
                    "token_id": token_record.id,
                },
            )
            obs_metrics.record_auth_email_change_confirm(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired token",
            )

        new_email = token_record.new_email

        # Re-check email availability at confirm time (race condition guard)
        existing_result = await db.execute(
            select(User).where(
                User.email.ilike(new_email),
                User.is_active.is_(True),
                User.id != token_record.user_id,
            )
        )
        if existing_result.scalar_one_or_none() is not None:
            outcome = OutcomeCode.IN_USE_AT_CONFIRM
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Email change confirm: email taken at confirm time",
                extra={
                    "event_name": AuthEvent.EMAIL_CHANGE_CONFIRMED,
                    "outcome_code": outcome,
                    "token_id": token_record.id,
                },
            )
            obs_metrics.record_auth_email_change_confirm(outcome)
            # Leave token unused so user can retry (race shouldn't consume the token)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use",
            )

        # Fetch the user
        user = await db.get(User, token_record.user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired token",
            )

        span.set_attribute("user.id", str(user.id))

        # Update user email and mark token used
        user.email = new_email
        token_record.used_at = now_utc

        # Invalidate sibling email-change tokens for this user
        sibling_result = await db.execute(
            select(EmailChangeToken).where(
                EmailChangeToken.user_id == token_record.user_id,
                EmailChangeToken.id != token_record.id,
                EmailChangeToken.used_at.is_(None),
            )
        )
        for sibling in sibling_result.scalars().all():
            sibling.used_at = now_utc

        await db.commit()

        outcome = OutcomeCode.SUCCESS
        span.set_attribute("outcome", outcome)
        _log.info(
            "Email change confirmed",
            extra={
                "event_name": AuthEvent.EMAIL_CHANGE_CONFIRMED,
                "outcome_code": outcome,
                "user_id": user.id,
                "token_id": token_record.id,
            },
        )
        obs_metrics.record_auth_email_change_confirm(outcome)
