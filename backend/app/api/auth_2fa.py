"""2FA enrolment, backup-code regeneration, and disable endpoints (SFBL-247).

Routes ship under ``/api/auth/2fa`` and require a regular authenticated JWT
(``get_current_user``). The forced-enrolment path via step-up ``mfa_pending``
tokens is added in SFBL-248.

Wire contract: ``docs/specs/2fa-totp.md`` §2.3, §2.5, §2.6.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.user_backup_code import UserBackupCode
from app.models.user_totp import UserTotp
from app.observability.events import AuthEvent, MfaEvent, OutcomeCode
from app.schemas.auth_2fa import (
    BackupCodesResponse,
    DisableRequest,
    EnrollConfirmRequest,
    EnrollConfirmResponse,
    EnrollStartResponse,
    RegenerateBackupCodesRequest,
)
from app.services.auth import create_access_token, get_current_user, verify_password
from app.services.totp import (
    TotpError,
    build_otpauth_uri,
    generate_backup_code,
    generate_secret,
    render_qr_svg,
    verify_code,
)
from app.utils.encryption import encrypt_secret, decrypt_secret

router = APIRouter(prefix="/api/auth/2fa", tags=["auth"])
_log = logging.getLogger(__name__)

#: Number of backup codes minted per enrolment / regenerate (spec §0 D9).
_BACKUP_CODE_COUNT = 10
#: bcrypt cost factor for backup-code hashes (spec §3.2).
_BACKUP_CODE_BCRYPT_ROUNDS = 12


def _hash_backup_code(plaintext_normalized: str) -> str:
    """bcrypt-hash a normalized (no-dash, uppercase) backup code."""
    return bcrypt.hashpw(
        plaintext_normalized.encode(),
        bcrypt.gensalt(rounds=_BACKUP_CODE_BCRYPT_ROUNDS),
    ).decode()


async def _mint_backup_codes(
    db: AsyncSession, *, user_id: str
) -> list[str]:
    """Insert a fresh set of backup codes; return the plaintext display forms.

    Caller is responsible for the surrounding transaction (including a delete
    of any prior codes when rotating).
    """
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


async def _resolve_jwt_expiry_minutes() -> int:
    """Read the current JWT lifetime from DB-backed settings with fallback."""
    try:
        from app.services.settings.service import settings_service as _svc  # noqa: PLC0415

        if _svc is not None:
            return int(await _svc.get("jwt_expiry_minutes"))
    except Exception:  # pragma: no cover - defensive; fall through to config
        _log.exception("Failed to read jwt_expiry_minutes from settings service")
    return settings.jwt_expiry_minutes


async def _require_tenant_can_disable() -> None:
    """Raise 403 ``tenant_enforced`` when ``require_2fa`` is on (spec §2.6)."""
    required = False
    try:
        from app.services.settings.service import settings_service as _svc  # noqa: PLC0415

        if _svc is not None:
            required = bool(await _svc.get("require_2fa"))
    except Exception:
        _log.exception("Failed to read require_2fa setting; defaulting to False")
        required = False
    if required:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": OutcomeCode.TENANT_ENFORCED,
                "message": "2FA is required for this tenant and cannot be disabled.",
            },
        )


async def _get_enrolment(db: AsyncSession, user_id: str) -> UserTotp | None:
    return (
        await db.execute(select(UserTotp).where(UserTotp.user_id == user_id))
    ).scalar_one_or_none()


# ─── /enroll/start ──────────────────────────────────────────────────────────


@router.post("/enroll/start", response_model=EnrollStartResponse)
async def enroll_start(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EnrollStartResponse:
    """Begin TOTP enrolment — return a fresh secret + QR for the authenticator.

    Stateless per spec §0 D11: nothing is persisted until ``/enroll/confirm``
    succeeds. If the user is already enrolled the endpoint returns 409 so the
    caller can route them to the "disable first" flow.
    """
    if settings.auth_mode == "none":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is not available in desktop mode",
        )

    existing = await _get_enrolment(db, current_user.id)
    if existing is not None:
        _log.info(
            "2FA enroll start rejected: already enrolled",
            extra={
                "event_name": MfaEvent.ENROLL_FAILED,
                "outcome_code": OutcomeCode.ALREADY_ENROLLED,
                "user_id": current_user.id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": OutcomeCode.ALREADY_ENROLLED,
                "message": "2FA is already enrolled for this account.",
            },
        )

    secret = generate_secret()
    otpauth_uri = build_otpauth_uri(
        secret_base32=secret,
        account_label=current_user.email,
        issuer="Salesforce Bulk Loader",
    )
    qr_svg = render_qr_svg(otpauth_uri)

    _log.info(
        "2FA enrolment started",
        extra={
            "event_name": MfaEvent.ENROLL_STARTED,
            "outcome_code": OutcomeCode.OK,
            "user_id": current_user.id,
        },
    )
    return EnrollStartResponse(
        secret_base32=secret, otpauth_uri=otpauth_uri, qr_svg=qr_svg
    )


# ─── /enroll/confirm ────────────────────────────────────────────────────────


@router.post("/enroll/confirm", response_model=EnrollConfirmResponse)
async def enroll_confirm(
    body: EnrollConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EnrollConfirmResponse:
    """Verify the first code + persist the factor + return a fresh JWT.

    The confirmation bumps ``User.password_changed_at`` so the caller's prior
    token is invalidated by ``get_current_user``'s watermark check — we issue
    a fresh token in the response so the frontend can continue transparently.
    """
    if settings.auth_mode == "none":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is not available in desktop mode",
        )

    if await _get_enrolment(db, current_user.id) is not None:
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
        _log.warning(
            "2FA enroll confirm rejected: invalid secret",
            extra={
                "event_name": MfaEvent.ENROLL_FAILED,
                "outcome_code": OutcomeCode.INVALID_SECRET,
                "user_id": current_user.id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": OutcomeCode.INVALID_SECRET,
                "message": "Enrolment secret is malformed.",
            },
        ) from exc

    if not result.ok:
        _log.warning(
            "2FA enroll confirm rejected: wrong code",
            extra={
                "event_name": MfaEvent.ENROLL_FAILED,
                "outcome_code": OutcomeCode.INVALID_CODE,
                "user_id": current_user.id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": OutcomeCode.INVALID_CODE,
                "message": "Verification code is invalid.",
            },
        )

    # Atomic: insert user_totp + backup codes + bump password_changed_at.
    now = datetime.now(timezone.utc)
    db.add(
        UserTotp(
            user_id=current_user.id,
            secret_encrypted=encrypt_secret(body.secret_base32),
            enrolled_at=now,
            last_used_at=now,
            last_used_counter=result.counter,
        )
    )
    display_codes = await _mint_backup_codes(db, user_id=current_user.id)
    # Re-load the persisted user inside the same session so we update the
    # canonical row (``current_user`` may be detached in tests).
    persisted = await db.get(User, current_user.id)
    if persisted is not None:
        persisted.password_changed_at = now
    await db.commit()

    _log.info(
        "2FA enrolment confirmed",
        extra={
            "event_name": MfaEvent.ENROLL_SUCCESS,
            "outcome_code": OutcomeCode.OK,
            "user_id": current_user.id,
        },
    )

    expiry = await _resolve_jwt_expiry_minutes()
    issue_for = persisted if persisted is not None else current_user
    return EnrollConfirmResponse(
        access_token=create_access_token(issue_for, expiry_minutes=expiry),
        expires_in=expiry * 60,
        backup_codes=display_codes,
    )


# ─── /backup-codes/regenerate ───────────────────────────────────────────────


@router.post("/backup-codes/regenerate", response_model=BackupCodesResponse)
async def regenerate_backup_codes(
    body: RegenerateBackupCodesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BackupCodesResponse:
    """Rotate the full backup-code set — requires a valid current TOTP code."""
    if settings.auth_mode == "none":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is not available in desktop mode",
        )

    enrolment = await _get_enrolment(db, current_user.id)
    if enrolment is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": OutcomeCode.INVALID_CODE,
                "message": "2FA is not enrolled for this account.",
            },
        )

    try:
        plaintext_secret = decrypt_secret(enrolment.secret_encrypted)
    except Exception as exc:  # EncryptionError
        _log.exception("Failed to decrypt stored TOTP secret")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to verify 2FA — contact an administrator.",
        ) from exc

    result = verify_code(
        secret_base32=plaintext_secret,
        code=body.code,
        last_used_counter=enrolment.last_used_counter,
    )
    if not result.ok:
        _log.warning(
            "Backup code regenerate rejected: wrong TOTP",
            extra={
                "event_name": AuthEvent.TOKEN_REJECTED,
                "outcome_code": OutcomeCode.WRONG_MFA,
                "user_id": current_user.id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": OutcomeCode.INVALID_CODE,
                "message": "Verification code is invalid.",
            },
        )

    # Rotate atomically: delete all existing codes, mint 10 fresh.
    await db.execute(
        delete(UserBackupCode).where(UserBackupCode.user_id == current_user.id)
    )
    display_codes = await _mint_backup_codes(db, user_id=current_user.id)
    # Advance replay counter so this code cannot be reused for a second action.
    enrolment.last_used_counter = result.counter
    enrolment.last_used_at = datetime.now(timezone.utc)
    await db.commit()

    _log.info(
        "Backup codes regenerated",
        extra={
            "event_name": MfaEvent.BACKUP_CODES_REGENERATED,
            "outcome_code": OutcomeCode.OK,
            "user_id": current_user.id,
        },
    )
    return BackupCodesResponse(backup_codes=display_codes)


# ─── /disable ───────────────────────────────────────────────────────────────


@router.post("/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_factor(
    body: DisableRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Disable the user's own 2FA factor.

    Requires password AND a current TOTP code (spec §2.6). Blocked with 403
    ``tenant_enforced`` when the global ``require_2fa`` setting is on.
    """
    if settings.auth_mode == "none":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is not available in desktop mode",
        )

    await _require_tenant_can_disable()

    persisted = await db.get(User, current_user.id)
    if persisted is None or not persisted.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": OutcomeCode.NO_LOCAL_AUTH,
                "message": "Password verification is unavailable for this account.",
            },
        )
    if not verify_password(body.password, persisted.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": OutcomeCode.WRONG_CURRENT,
                "message": "Password is incorrect.",
            },
        )

    enrolment = await _get_enrolment(db, current_user.id)
    if enrolment is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": OutcomeCode.INVALID_CODE,
                "message": "2FA is not enrolled for this account.",
            },
        )

    try:
        plaintext_secret = decrypt_secret(enrolment.secret_encrypted)
    except Exception as exc:
        _log.exception("Failed to decrypt stored TOTP secret")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to verify 2FA — contact an administrator.",
        ) from exc

    result = verify_code(
        secret_base32=plaintext_secret,
        code=body.code,
        last_used_counter=enrolment.last_used_counter,
    )
    if not result.ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": OutcomeCode.INVALID_CODE,
                "message": "Verification code is invalid.",
            },
        )

    # Drop factor + backup codes atomically.
    await db.execute(
        delete(UserBackupCode).where(UserBackupCode.user_id == current_user.id)
    )
    await db.execute(
        delete(UserTotp).where(UserTotp.user_id == current_user.id)
    )
    await db.commit()

    _log.info(
        "2FA factor disabled by user",
        extra={
            "event_name": MfaEvent.FACTOR_DISABLED,
            "outcome_code": OutcomeCode.OK,
            "user_id": current_user.id,
        },
    )
