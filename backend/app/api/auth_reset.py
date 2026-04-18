"""Unauthenticated password-reset flow — SFBL-147.

Endpoints:
  POST /api/auth/password-reset/request  — issue a reset token and send email
  POST /api/auth/password-reset/confirm  — redeem token and set new password
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.observability.events import AuthEvent, OutcomeCode
from app.schemas.auth import PasswordResetConfirm, PasswordResetRequest
from app.services.auth import (
    PasswordPolicyError,
    hash_password,
    validate_password_strength,
)
from app.services.email import EmailCategory, EmailService, get_email_service
from app.services.rate_limit import (
    check_and_record,
    hashed_email_key,
    ip_key,
    rate_limit,
)

router = APIRouter(prefix="/api/auth/password-reset", tags=["auth"])

_log = logging.getLogger(__name__)

# ── Rate-limit dependencies ────────────────────────────────────────────────────

_ip_limiter = rate_limit(
    ip_key,
    limit=settings.pw_reset_rate_limit_per_ip_hour,
    window_seconds=3600,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _email_hash(email: str) -> str:
    """SHA-256 hex of lowercased email — used only in log fields, never stored."""
    return hashlib.sha256(email.lower().encode()).hexdigest()


def _reset_url(raw_token: str, request: Request) -> str:
    """Build the frontend reset URL from config or request origin."""
    base = settings.frontend_base_url
    if not base:
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin:
            # Strip any trailing path from referer
            from urllib.parse import urlparse
            parsed = urlparse(origin)
            base = f"{parsed.scheme}://{parsed.netloc}"
        else:
            base = ""
        _log.warning(
            "FRONTEND_BASE_URL not configured; falling back to request origin %r "
            "for password-reset link. Set FRONTEND_BASE_URL in .env.",
            base,
            extra={
                "event_name": AuthEvent.PASSWORD_RESET_REQUESTED,
                "outcome_code": OutcomeCode.CONFIGURATION_ERROR,
            },
        )
    return f"{base}/reset-password/{raw_token}"


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/request", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    body: PasswordResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
    _ip: None = Depends(_ip_limiter),
) -> None:
    """Initiate a password-reset flow.

    Always returns 202 regardless of whether the email matches a user account
    (non-enumeration guarantee).
    """
    email_address = str(body.email).lower()
    email_h = _email_hash(email_address)

    # Per-email rate limit (checked manually so we can use the email from body)
    email_rl_key = hashed_email_key(email_address)
    allowed = await check_and_record(
        email_rl_key,
        limit=settings.pw_reset_rate_limit_per_email_hour,
        window_seconds=3600,
    )
    if not allowed:
        _log.warning(
            "Password reset request rate-limited (per-email)",
            extra={
                "event_name": AuthEvent.PASSWORD_RESET_REQUESTED,
                "outcome_code": OutcomeCode.RATE_LIMITED,
                "email_hash": email_h,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
        )

    # Look up user — silently ignore unknown addresses
    result = await db.execute(select(User).where(User.email == email_address))
    user: Optional[User] = result.scalars().first()

    if user is None or not user.is_active:
        _log.info(
            "Password reset requested for unknown/inactive email",
            extra={
                "event_name": AuthEvent.PASSWORD_RESET_REQUESTED,
                "outcome_code": OutcomeCode.UNKNOWN_EMAIL,
                "email_hash": email_h,
            },
        )
        return  # 202 — non-enumeration

    # Generate raw token and hash for storage
    raw_token = secrets.token_urlsafe(32)
    token_hash = _sha256_hex(raw_token)
    now_utc = datetime.now(timezone.utc)
    expires_at = now_utc.replace(microsecond=0)
    from datetime import timedelta
    expires_at = now_utc + timedelta(minutes=settings.password_reset_ttl_minutes)

    token_row = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
        created_at=now_utc,
        request_ip=(request.client.host if request.client else None),
    )
    db.add(token_row)
    await db.commit()
    await db.refresh(token_row)

    reset_url = _reset_url(raw_token, request)
    display_name = user.display_name or user.username or user.email or "User"

    await email_service.send_template(
        "auth/password_reset",
        {
            "user_display_name": display_name,
            "reset_url": reset_url,
            "expires_in_minutes": settings.password_reset_ttl_minutes,
        },
        to=user.email,  # type: ignore[arg-type]
        category=EmailCategory.AUTH,
        idempotency_key=f"pw-reset:{token_row.id}",
    )

    _log.info(
        "Password reset email sent",
        extra={
            "event_name": AuthEvent.PASSWORD_RESET_REQUESTED,
            "outcome_code": OutcomeCode.SENT,
            "email_hash": email_h,
            "token_id": token_row.id,
        },
    )


@router.post("/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_password_reset(
    body: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Redeem a password-reset token and set a new password."""
    token_hash = _sha256_hex(body.token)
    # First 8 chars of hash for safe logging — never log raw token
    token_hash_prefix = token_hash[:8]

    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    token_row: Optional[PasswordResetToken] = result.scalars().first()

    now_utc = datetime.now(timezone.utc)

    if token_row is None:
        _log.warning(
            "Password reset confirm: token not found",
            extra={
                "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
                "outcome_code": OutcomeCode.INVALID_TOKEN,
                "token_hash_prefix": token_hash_prefix,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token",
        )

    # Ensure token_row.expires_at is timezone-aware
    expires_at = token_row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at < now_utc:
        _log.warning(
            "Password reset confirm: token expired",
            extra={
                "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
                "outcome_code": OutcomeCode.EXPIRED_TOKEN,
                "token_id": token_row.id,
                "token_hash_prefix": token_hash_prefix,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token",
        )

    if token_row.used_at is not None:
        _log.warning(
            "Password reset confirm: token already used",
            extra={
                "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
                "outcome_code": OutcomeCode.USED_TOKEN,
                "token_id": token_row.id,
                "token_hash_prefix": token_hash_prefix,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token",
        )

    # Load the associated user
    user = await db.get(User, token_row.user_id)
    if user is None or not user.is_active:
        _log.warning(
            "Password reset confirm: user not found or inactive",
            extra={
                "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
                "outcome_code": OutcomeCode.INVALID_TOKEN,
                "token_id": token_row.id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token",
        )

    if user.hashed_password is None:
        _log.warning(
            "Password reset confirm: SAML-only account",
            extra={
                "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
                "outcome_code": OutcomeCode.NO_LOCAL_AUTH,
                "token_id": token_row.id,
                "user_id": user.id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password reset not available for this account",
        )

    try:
        validate_password_strength(body.new_password)
    except PasswordPolicyError as exc:
        _log.warning(
            "Password reset confirm: policy violation",
            extra={
                "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
                "outcome_code": OutcomeCode.POLICY_VIOLATION,
                "token_id": token_row.id,
                "user_id": user.id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # Single transaction: update password, watermark, mark token used,
    # and invalidate all other unused reset tokens for this user.
    user.hashed_password = hash_password(body.new_password)
    user.password_changed_at = now_utc.replace(microsecond=0)
    token_row.used_at = now_utc

    # Mark all sibling unused reset tokens for this user as used
    await db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.id != token_row.id,
        )
        .values(used_at=now_utc)
    )

    await db.commit()

    _log.info(
        "Password reset confirmed",
        extra={
            "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
            "outcome_code": OutcomeCode.SUCCESS,
            "token_id": token_row.id,
            "user_id": user.id,
        },
    )
