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
from app.observability import metrics as obs_metrics
from app.observability import tracing
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

async def _get_pw_reset_ip_limit() -> int:
    """Resolve pw_reset_rate_limit_per_ip_hour from DB-backed settings (SFBL-156).

    # value applies to new rate-limit windows; existing in-flight windows use the value active when they started
    """
    from app.services.settings.service import settings_service as _svc
    if _svc is not None:
        return await _svc.get("pw_reset_rate_limit_per_ip_hour")
    return settings.pw_reset_rate_limit_per_ip_hour


# Note: _ip_limiter is a FastAPI dependency that reads the limit at request time.
# The limit value is fetched from DB-backed settings per-request so changes take
# effect for new windows immediately.
async def _ip_limiter(request: Request) -> None:  # type: ignore[misc]
    """Per-IP rate limit dependency for password-reset endpoints."""
    ip = request.client.host if request.client else "unknown"
    rate_key = ip_key(request)
    limit = await _get_pw_reset_ip_limit()
    # value applies to new rate-limit windows; existing in-flight windows use the value active when they started
    allowed = await check_and_record(rate_key, limit, 3600)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _email_hash(email: str) -> str:
    """SHA-256 hex of lowercased email — used only in log fields, never stored."""
    return hashlib.sha256(email.lower().encode()).hexdigest()


async def _get_frontend_base_url() -> str:
    """Resolve frontend_base_url from SettingsService."""
    from app.services.settings.service import settings_service as _svc
    if _svc is not None:
        return (await _svc.get("frontend_base_url")) or ""
    return ""


async def _reset_url(raw_token: str, request: Request) -> str:
    """Build the frontend reset URL from config or request origin."""
    base = await _get_frontend_base_url()
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
    with tracing.auth_password_reset_request_span() as span:
        email_address = str(body.email).lower()
        email_h = _email_hash(email_address)

        # Per-email rate limit (checked manually so we can use the email from body)
        # value applies to new rate-limit windows; existing in-flight windows use the value active when they started
        from app.services.settings.service import settings_service as _svc
        _email_rl_limit: int = (
            await _svc.get("pw_reset_rate_limit_per_email_hour") if _svc is not None
            else settings.pw_reset_rate_limit_per_email_hour
        )
        email_rl_key = hashed_email_key(email_address)
        allowed = await check_and_record(
            email_rl_key,
            limit=_email_rl_limit,
            window_seconds=3600,
        )
        if not allowed:
            outcome = OutcomeCode.RATE_LIMITED
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Password reset request rate-limited (per-email)",
                extra={
                    "event_name": AuthEvent.PASSWORD_RESET_REQUESTED,
                    "outcome_code": outcome,
                    "email_hash": email_h,
                },
            )
            obs_metrics.record_auth_password_reset_request(outcome)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests",
            )

        # Look up user — silently ignore unknown addresses
        result = await db.execute(select(User).where(User.email == email_address))
        user: Optional[User] = result.scalars().first()

        if user is None or not user.is_active:
            outcome = OutcomeCode.UNKNOWN_EMAIL
            span.set_attribute("outcome", outcome)
            _log.info(
                "Password reset requested for unknown/inactive email",
                extra={
                    "event_name": AuthEvent.PASSWORD_RESET_REQUESTED,
                    "outcome_code": outcome,
                    "email_hash": email_h,
                },
            )
            obs_metrics.record_auth_password_reset_request(outcome)
            return  # 202 — non-enumeration

        # Generate raw token and hash for storage
        raw_token = secrets.token_urlsafe(32)
        token_hash = _sha256_hex(raw_token)
        now_utc = datetime.now(timezone.utc)
        from datetime import timedelta
        _pw_reset_ttl: int = (
            await _svc.get("password_reset_ttl_minutes") if _svc is not None
            else settings.password_reset_ttl_minutes
        )
        expires_at = now_utc + timedelta(minutes=_pw_reset_ttl)

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

        reset_url = await _reset_url(raw_token, request)
        display_name = user.display_name or user.email or "User"

        await email_service.send_template(
            "auth/password_reset",
            {
                "user_display_name": display_name,
                "reset_url": reset_url,
                "expires_in_minutes": _pw_reset_ttl,
            },
            to=user.email,  # type: ignore[arg-type]
            category=EmailCategory.AUTH,
            idempotency_key=f"pw-reset:{token_row.id}",
        )

        outcome = OutcomeCode.SENT
        span.set_attribute("outcome", outcome)
        span.set_attribute("user.id", str(user.id))
        _log.info(
            "Password reset email sent",
            extra={
                "event_name": AuthEvent.PASSWORD_RESET_REQUESTED,
                "outcome_code": outcome,
                "email_hash": email_h,
                "token_id": token_row.id,
            },
        )
        obs_metrics.record_auth_password_reset_request(outcome)


@router.post("/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_password_reset(
    body: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Redeem a password-reset token and set a new password."""
    with tracing.auth_password_reset_confirm_span() as span:
        token_hash = _sha256_hex(body.token)
        # First 8 chars of hash for safe logging — never log raw token
        token_hash_prefix = token_hash[:8]

        result = await db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
        token_row: Optional[PasswordResetToken] = result.scalars().first()

        now_utc = datetime.now(timezone.utc)

        if token_row is None:
            outcome = OutcomeCode.INVALID_TOKEN
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Password reset confirm: token not found",
                extra={
                    "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
                    "outcome_code": outcome,
                    "token_hash_prefix": token_hash_prefix,
                },
            )
            obs_metrics.record_auth_password_reset_confirm(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired token",
            )

        # Ensure token_row.expires_at is timezone-aware
        expires_at = token_row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at < now_utc:
            outcome = OutcomeCode.EXPIRED_TOKEN
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Password reset confirm: token expired",
                extra={
                    "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
                    "outcome_code": outcome,
                    "token_id": token_row.id,
                    "token_hash_prefix": token_hash_prefix,
                },
            )
            obs_metrics.record_auth_password_reset_confirm(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired token",
            )

        if token_row.used_at is not None:
            outcome = OutcomeCode.USED_TOKEN
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Password reset confirm: token already used",
                extra={
                    "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
                    "outcome_code": outcome,
                    "token_id": token_row.id,
                    "token_hash_prefix": token_hash_prefix,
                },
            )
            obs_metrics.record_auth_password_reset_confirm(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired token",
            )

        # Load the associated user
        user = await db.get(User, token_row.user_id)
        if user is None or not user.is_active:
            outcome = OutcomeCode.INVALID_TOKEN
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Password reset confirm: user not found or inactive",
                extra={
                    "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
                    "outcome_code": outcome,
                    "token_id": token_row.id,
                },
            )
            obs_metrics.record_auth_password_reset_confirm(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired token",
            )

        span.set_attribute("user.id", str(user.id))

        if user.hashed_password is None:
            outcome = OutcomeCode.NO_LOCAL_AUTH
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Password reset confirm: SAML-only account",
                extra={
                    "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
                    "outcome_code": outcome,
                    "token_id": token_row.id,
                    "user_id": user.id,
                },
            )
            obs_metrics.record_auth_password_reset_confirm(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password reset not available for this account",
            )

        try:
            validate_password_strength(body.new_password)
        except PasswordPolicyError as exc:
            outcome = OutcomeCode.POLICY_VIOLATION
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Password reset confirm: policy violation",
                extra={
                    "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
                    "outcome_code": outcome,
                    "token_id": token_row.id,
                    "user_id": user.id,
                },
            )
            obs_metrics.record_auth_password_reset_confirm(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        # Atomically consume the token via conditional UPDATE so concurrent
        # /confirm calls cannot both succeed. Only one will flip used_at from
        # NULL → now_utc and see rowcount == 1; the loser sees rowcount == 0
        # and is rejected as an already-used token.
        consume_result = await db.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.id == token_row.id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=now_utc)
        )
        if consume_result.rowcount != 1:
            await db.rollback()
            outcome = OutcomeCode.USED_TOKEN
            span.set_attribute("outcome", outcome)
            _log.warning(
                "Password reset confirm: token consumed concurrently",
                extra={
                    "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
                    "outcome_code": outcome,
                    "token_id": token_row.id,
                    "token_hash_prefix": token_hash_prefix,
                },
            )
            obs_metrics.record_auth_password_reset_confirm(outcome)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired token",
            )

        # Token successfully consumed — apply the password change, bump the
        # watermark, and invalidate sibling unused tokens for this user.
        user.hashed_password = hash_password(body.new_password)
        user.password_changed_at = now_utc.replace(microsecond=0)

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

        outcome = OutcomeCode.SUCCESS
        span.set_attribute("outcome", outcome)
        _log.info(
            "Password reset confirmed",
            extra={
                "event_name": AuthEvent.PASSWORD_RESET_CONFIRMED,
                "outcome_code": outcome,
                "token_id": token_row.id,
                "user_id": user.id,
            },
        )
        obs_metrics.record_auth_password_reset_confirm(outcome)
