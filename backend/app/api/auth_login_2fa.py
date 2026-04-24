"""Two-phase login endpoints — TOTP challenge + forced enrolment (SFBL-248).

Routes ship under ``/api/auth/login/2fa`` and are gated by
:func:`app.services.auth.get_mfa_pending_user`. They consume the short-lived
``mfa_pending`` token minted by ``POST /api/auth/login`` and — on success —
fire all phase-2 side effects (``handle_successful_login`` + success
``login_attempt`` row + ``auth.login.succeeded`` event + ``must_reset_password``
handling) via the shared ``_login_success_phase2`` helper in ``api/auth.py``.

Wire contract: ``docs/specs/2fa-totp.md`` §2.2 / §2.3 / §2.4.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.user_backup_code import UserBackupCode
from app.models.user_totp import UserTotp
from app.observability.events import AuthEvent, MfaEvent, OutcomeCode
from app.schemas.auth import TokenResponse
from app.schemas.auth_2fa import (
    Login2FAEnrollAndVerifyRequest,
    Login2FAEnrollAndVerifyResponse,
    Login2FAEnrollStartResponse,
    Login2FARequest,
)
from app.services.auth import get_mfa_pending_user
from app.services.auth_lockout import handle_failed_attempt
from app.services.rate_limit import check_and_record
from app.services.totp import (
    TotpError,
    build_otpauth_uri,
    generate_backup_code,
    generate_secret,
    render_qr_svg,
    verify_code,
)
from app.utils.encryption import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/api/auth/login/2fa", tags=["auth"])
_log = logging.getLogger(__name__)

#: Per-user verify budget — matches spec §10.2.
_USER_RATE_LIMIT_ATTEMPTS = 10
_USER_RATE_LIMIT_WINDOW_SECONDS = 300

#: Number of backup codes minted on forced enrolment (spec §0 D9).
_BACKUP_CODE_COUNT = 10
_BACKUP_CODE_BCRYPT_ROUNDS = 12


def _ip_from_request(request: Request) -> str:
    return (request.client.host if request.client else None) or "unknown"


def _ua_from_request(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _hash_backup_code(plaintext_normalized: str) -> str:
    return bcrypt.hashpw(
        plaintext_normalized.encode(),
        bcrypt.gensalt(rounds=_BACKUP_CODE_BCRYPT_ROUNDS),
    ).decode()


async def _mint_backup_codes(db: AsyncSession, *, user_id: str) -> list[str]:
    display_codes: list[str] = []
    for _ in range(_BACKUP_CODE_COUNT):
        display = generate_backup_code()
        normalized = display.replace("-", "").upper()
        db.add(
            UserBackupCode(
                user_id=user_id,
                code_hash=_hash_backup_code(normalized),
            )
        )
        display_codes.append(display)
    return display_codes


def _must_enroll_only(
    mfa: tuple[User, bool],
) -> User:
    """Reject callers whose token does not carry ``must_enroll=true``."""
    user, must_enroll = mfa
    if not must_enroll:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "not_forced_enroll",
                "message": "This endpoint requires a forced-enrolment MFA token.",
            },
        )
    return user


def _must_enroll_false_only(
    mfa: tuple[User, bool],
) -> User:
    """Reject callers whose token carries ``must_enroll=true``.

    ``/login/2fa`` is the challenge endpoint for an already-enrolled user —
    a forced-enrolment token should be routed to ``/enroll-and-verify``.
    """
    user, must_enroll = mfa
    if must_enroll:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": OutcomeCode.MFA_TOKEN_INVALID,
                "message": "Token is a forced-enrolment token; call /enroll-and-verify.",
            },
        )
    return user


async def _enforce_user_rate_limit(
    user_id: str, *, ip: str, email: str
) -> None:
    key = f"2fa:user:{user_id}"
    allowed = await check_and_record(
        key, _USER_RATE_LIMIT_ATTEMPTS, _USER_RATE_LIMIT_WINDOW_SECONDS
    )
    if not allowed:
        _log.warning(
            "2FA verify rate-limited",
            extra={
                "event_name": AuthEvent.LOGIN_RATE_LIMITED,
                "outcome_code": OutcomeCode.MFA_USER_LIMIT,
                "user_id": user_id,
                "ip": ip,
                "email": email,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": OutcomeCode.MFA_USER_LIMIT,
                "message": "Too many 2FA attempts — please wait and try again.",
            },
        )


async def _reject_mfa_code(
    db: AsyncSession,
    user: User,
    *,
    ip: str,
    ua: str | None,
    email: str,
    outcome: str,
) -> None:
    """Record a failed 2FA verification + advance lockout counters, then 401."""
    from app.api.auth import _persist_login_attempt  # local import avoids cycle

    _log.warning(
        "2FA verification failed",
        extra={
            "event_name": MfaEvent.LOGIN_TOTP_FAILURE,
            "outcome_code": outcome,
            "user_id": user.id,
            "ip": ip,
            "email": email,
        },
    )
    await _persist_login_attempt(
        db,
        email=email,
        ip=ip,
        user_agent=ua,
        outcome=outcome,
        user_id=user.id,
    )
    await db.flush()
    await handle_failed_attempt(db, user, ip=ip, username=email, user_agent=ua)
    await db.commit()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "mfa_code_invalid",
            "message": "2FA verification failed.",
        },
    )


# ─── /login/2fa ─────────────────────────────────────────────────────────────


@router.post("", response_model=TokenResponse)
async def login_2fa(
    body: Login2FARequest,
    request: Request,
    mfa: tuple[User, bool] = Depends(get_mfa_pending_user),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Phase-2 login: verify TOTP or a backup code against the pending user."""
    from app.api.auth import _login_success_phase2  # local import avoids cycle

    user = _must_enroll_false_only(mfa)
    ip = _ip_from_request(request)
    ua = _ua_from_request(request)
    email = user.email or ""

    await _enforce_user_rate_limit(user.id, ip=ip, email=email)

    if body.method == "totp":
        enrolment = (
            await db.execute(select(UserTotp).where(UserTotp.user_id == user.id))
        ).scalar_one_or_none()
        if enrolment is None:
            # Token said "enrolled" but no row — treat as invalid token.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": OutcomeCode.MFA_TOKEN_INVALID,
                    "message": "MFA challenge token is invalid.",
                },
            )
        try:
            plaintext_secret = decrypt_secret(enrolment.secret_encrypted)
        except Exception as exc:  # pragma: no cover - defensive
            _log.exception("Failed to decrypt stored TOTP secret")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to verify 2FA — contact an administrator.",
            ) from exc

        try:
            result = verify_code(
                secret_base32=plaintext_secret,
                code=body.code,
                last_used_counter=enrolment.last_used_counter,
            )
        except TotpError:
            result = None

        if result is None or not result.ok:
            await _reject_mfa_code(
                db, user, ip=ip, ua=ua, email=email, outcome=OutcomeCode.WRONG_MFA
            )

        # Anti-replay: advance counter before phase-2 side effects.
        enrolment.last_used_counter = result.counter
        enrolment.last_used_at = datetime.now(timezone.utc)
        await db.flush()

        _log.info(
            "2FA TOTP login succeeded",
            extra={
                "event_name": MfaEvent.LOGIN_TOTP_SUCCESS,
                "outcome_code": OutcomeCode.MFA_OK,
                "user_id": user.id,
                "ip": ip,
                "email": email,
            },
        )
        return await _login_success_phase2(
            db, user, ip=ip, ua=ua, email=email
        )

    # ── method == "backup_code" ──────────────────────────────────────────
    normalized = body.code.replace("-", "").upper().strip()
    if not normalized:
        await _reject_mfa_code(
            db, user, ip=ip, ua=ua, email=email, outcome=OutcomeCode.WRONG_MFA
        )

    # Fetch ALL unconsumed codes and iterate the full set to avoid timing leaks.
    rows = (
        await db.execute(
            select(UserBackupCode).where(
                UserBackupCode.user_id == user.id,
                UserBackupCode.consumed_at.is_(None),
            )
        )
    ).scalars().all()

    matched: UserBackupCode | None = None
    candidate = normalized.encode()
    for row in rows:
        try:
            ok = bcrypt.checkpw(candidate, row.code_hash.encode())
        except ValueError:
            ok = False
        if ok and matched is None:
            matched = row
        # Do NOT break — full iteration preserves constant time.

    if matched is None:
        await _reject_mfa_code(
            db, user, ip=ip, ua=ua, email=email, outcome=OutcomeCode.WRONG_MFA
        )

    matched.consumed_at = datetime.now(timezone.utc)
    matched.consumed_ip = ip
    await db.flush()

    remaining = len(rows) - 1
    if remaining == 0:
        _log.warning(
            "Backup codes exhausted",
            extra={
                "event_name": MfaEvent.BACKUP_CODES_EXHAUSTED,
                "outcome_code": OutcomeCode.MFA_BACKUP_CODES_EXHAUSTED,
                "user_id": user.id,
            },
        )
    _log.info(
        "2FA backup-code login succeeded",
        extra={
            "event_name": MfaEvent.LOGIN_BACKUP_CODE_USED,
            "outcome_code": OutcomeCode.BACKUP_CODE_USED,
            "user_id": user.id,
            "ip": ip,
            "email": email,
            "backup_codes_remaining": remaining,
        },
    )
    return await _login_success_phase2(db, user, ip=ip, ua=ua, email=email)


# ─── /login/2fa/enroll/start ────────────────────────────────────────────────


@router.post("/enroll/start", response_model=Login2FAEnrollStartResponse)
async def login_2fa_enroll_start(
    mfa: tuple[User, bool] = Depends(get_mfa_pending_user),
) -> Login2FAEnrollStartResponse:
    """Forced-enrolment start — mint a secret + QR for an unauthenticated user."""
    user = _must_enroll_only(mfa)

    secret = generate_secret()
    otpauth_uri = build_otpauth_uri(
        secret_base32=secret,
        account_label=user.email or user.id,
        issuer="Salesforce Bulk Loader",
    )
    qr_svg = render_qr_svg(otpauth_uri)

    _log.info(
        "Forced 2FA enrolment started",
        extra={
            "event_name": AuthEvent.LOGIN_MFA_ENROLL_STARTED,
            "outcome_code": OutcomeCode.OK,
            "user_id": user.id,
        },
    )
    return Login2FAEnrollStartResponse(
        secret_base32=secret,
        otpauth_uri=otpauth_uri,
        qr_svg=qr_svg,
    )


# ─── /login/2fa/enroll-and-verify ───────────────────────────────────────────


@router.post("/enroll-and-verify", response_model=Login2FAEnrollAndVerifyResponse)
async def login_2fa_enroll_and_verify(
    body: Login2FAEnrollAndVerifyRequest,
    request: Request,
    mfa: tuple[User, bool] = Depends(get_mfa_pending_user),
    db: AsyncSession = Depends(get_db),
) -> Login2FAEnrollAndVerifyResponse:
    """Forced-enrolment confirm — verify code, persist factor, issue full token."""
    from app.api.auth import _login_success_phase2  # local import avoids cycle

    user = _must_enroll_only(mfa)
    ip = _ip_from_request(request)
    ua = _ua_from_request(request)
    email = user.email or ""

    # Race: another session may have enrolled this user between phase 1 and now.
    existing = (
        await db.execute(select(UserTotp).where(UserTotp.user_id == user.id))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": OutcomeCode.ALREADY_ENROLLED,
                "message": "2FA is already enrolled for this account.",
            },
        )

    try:
        result = verify_code(secret_base32=body.secret_base32, code=body.code)
    except TotpError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": OutcomeCode.INVALID_SECRET,
                "message": "Enrolment secret is malformed.",
            },
        ) from exc

    if not result.ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": OutcomeCode.INVALID_CODE,
                "message": "Verification code is invalid.",
            },
        )

    now = datetime.now(timezone.utc)
    db.add(
        UserTotp(
            user_id=user.id,
            secret_encrypted=encrypt_secret(body.secret_base32),
            enrolled_at=now,
            last_used_at=now,
            last_used_counter=result.counter,
        )
    )
    display_codes = await _mint_backup_codes(db, user_id=user.id)
    # Re-load persisted row so we bump watermark on the canonical instance.
    persisted = await db.get(User, user.id)
    if persisted is not None:
        persisted.password_changed_at = now
    await db.flush()

    _log.info(
        "Forced 2FA enrolment confirmed",
        extra={
            "event_name": MfaEvent.ENROLL_SUCCESS,
            "outcome_code": OutcomeCode.OK,
            "user_id": user.id,
        },
    )

    success_for = persisted if persisted is not None else user
    token_resp = await _login_success_phase2(
        db, success_for, ip=ip, ua=ua, email=email
    )
    return Login2FAEnrollAndVerifyResponse(
        access_token=token_resp.access_token,
        expires_in=token_resp.expires_in,
        must_reset_password=token_resp.must_reset_password,
        mfa_required=False,
        backup_codes=display_codes,
    )


__all__ = ["router"]
