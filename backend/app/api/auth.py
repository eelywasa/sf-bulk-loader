"""Auth API — login, session inspection, and auth configuration."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.observability.events import AuthEvent, OutcomeCode
from app.observability.metrics import record_auth_login_attempt
from app.models.user_backup_code import UserBackupCode
from app.models.user_totp import UserTotp
from app.schemas.auth import (
    AuthConfigResponse,
    LoginRequest,
    MfaRequiredResponse,
    MfaStatus,
    ProfileSummary,
    TokenResponse,
    UserResponse,
)
from app.services.auth import (
    create_access_token,
    create_mfa_token,
    get_current_user,
    verify_password,
)
from app.services.auth_lockout import handle_failed_attempt, handle_successful_login
from app.services.rate_limit import check_and_record

router = APIRouter(prefix="/api/auth", tags=["auth"])

_log = logging.getLogger(__name__)


def _ip_from_request(request: Request) -> str:
    """Return the client IP address, falling back to 'unknown'."""
    return (request.client.host if request.client else None) or "unknown"


def _user_agent_from_request(request: Request) -> str | None:
    """Return the User-Agent header value, or None if absent."""
    return request.headers.get("user-agent")


async def _persist_login_attempt(
    db: AsyncSession,
    *,
    email: str,
    ip: str,
    user_agent: str | None,
    outcome: str,
    user_id: str | None,
) -> None:
    """Persist a LoginAttempt row.  Errors are logged but never propagate.

    The insert runs inside a SAVEPOINT so a flush failure rolls back only the
    login_attempt write, leaving any outer-transaction mutations (e.g. lockout
    counter updates in SFBL-191) intact for the subsequent ``db.commit()``.
    """
    try:
        async with db.begin_nested():
            attempt = LoginAttempt(
                user_id=user_id,
                username=email,  # username column stores the submitted email for audit trail
                ip=ip,
                user_agent=user_agent,
                outcome=outcome,
                attempted_at=datetime.now(timezone.utc),
            )
            db.add(attempt)
    except Exception:
        _log.exception(
            "Failed to persist login attempt row",
            extra={
                "event_name": AuthEvent.LOGIN_FAILED,
                "outcome_code": OutcomeCode.DATABASE_ERROR,
                "email": email,
                "ip": ip,
            },
        )


async def _login_success_phase2(
    db: AsyncSession,
    user: User,
    *,
    ip: str,
    ua: str | None,
    email: str,
) -> TokenResponse:
    """Apply all "login succeeded" side effects and mint a full-access JWT.

    Shared between ``POST /api/auth/login`` (when MFA is not in play) and the
    ``/api/auth/login/2fa`` family (SFBL-248). Resets lockout counters, writes
    a success ``login_attempt`` row, emits ``auth.login.succeeded``, honours
    ``must_reset_password``, and commits the transaction.
    """
    must_reset = user.must_reset_password
    outcome_code = OutcomeCode.MUST_RESET_PASSWORD if must_reset else OutcomeCode.OK
    _log.info(
        "Login succeeded (must reset password)" if must_reset else "Login succeeded",
        extra={
            "event_name": AuthEvent.LOGIN_SUCCEEDED,
            "outcome_code": outcome_code,
            "ip": ip,
            "email": email,
            "user_id": user.id,
        },
    )
    await handle_successful_login(db, user)
    user.last_login_at = datetime.now(timezone.utc)
    record_auth_login_attempt(outcome_code)
    await _persist_login_attempt(
        db,
        email=email,
        ip=ip,
        user_agent=ua,
        outcome=outcome_code,
        user_id=user.id,
    )
    await db.commit()

    from app.services.settings.service import settings_service as _svc
    _jwt_expiry: int = (
        await _svc.get("jwt_expiry_minutes") if _svc is not None
        else settings.jwt_expiry_minutes
    )
    return TokenResponse(
        access_token=create_access_token(user, expiry_minutes=_jwt_expiry),
        expires_in=_jwt_expiry * 60,
        must_reset_password=must_reset,
        mfa_required=False,
    )


@router.post("/login", response_model=None)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse | MfaRequiredResponse:
    """Authenticate with email + password and return a JWT.

    SFBL-198: login identifier is now email (was username). Old {username, password}
    shape returns 422 automatically because the schema requires an ``email`` field.

    Every attempt — success or failure — is persisted to ``login_attempt``
    and emitted as a structured log event.

    Per-IP rate limit: ``LOGIN_RATE_LIMIT_ATTEMPTS`` (default 20) attempts
    per ``LOGIN_RATE_LIMIT_WINDOW_SECONDS`` (default 300 s).  The limit is
    per-process only — see ``services/rate_limit.py`` for the in-memory
    implementation note.  On breach the attempt row is still persisted and
    HTTP 429 is returned before any credential check.
    """
    ip = _ip_from_request(request)
    ua = _user_agent_from_request(request)
    email = str(body.email)

    # ── Per-IP rate limit check ───────────────────────────────────────────────
    # Read rate-limit params from DB-backed settings (SFBL-156).
    # value applies to new rate-limit windows; existing in-flight windows use the value active when they started
    from app.services.settings.service import settings_service as _svc
    _rl_attempts: int = (
        await _svc.get("login_rate_limit_attempts") if _svc is not None
        else settings.login_rate_limit_attempts
    )
    _rl_window: int = (
        await _svc.get("login_rate_limit_window_seconds") if _svc is not None
        else settings.login_rate_limit_window_seconds
    )
    rate_key = f"login:ip:{ip}"
    allowed = await check_and_record(
        rate_key,
        _rl_attempts,
        _rl_window,
    )
    if not allowed:
        _log.warning(
            "Login rate limit exceeded",
            extra={
                "event_name": AuthEvent.LOGIN_RATE_LIMITED,
                "outcome_code": OutcomeCode.IP_LIMIT,
                "ip": ip,
                "email": email,
            },
        )
        record_auth_login_attempt(OutcomeCode.IP_LIMIT)
        await _persist_login_attempt(
            db,
            email=email,
            ip=ip,
            user_agent=ua,
            outcome=OutcomeCode.IP_LIMIT,
            user_id=None,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts — please try again later",
        )

    # ── Look up user ──────────────────────────────────────────────────────────
    result = await db.execute(select(User).where(User.email == email))
    user: User | None = result.scalars().first()

    if user is None:
        # Unknown email — persist with user_id=null
        _log.warning(
            "Login failed: unknown user",
            extra={
                "event_name": AuthEvent.LOGIN_FAILED,
                "outcome_code": OutcomeCode.UNKNOWN_USER,
                "ip": ip,
                "email": email,
            },
        )
        record_auth_login_attempt(OutcomeCode.UNKNOWN_USER)
        await _persist_login_attempt(
            db,
            email=email,
            ip=ip,
            user_agent=ua,
            outcome=OutcomeCode.UNKNOWN_USER,
            user_id=None,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # ── Check status before password verification ─────────────────────────────

    # Check tier-1 auto-lockout (locked_until still in the future).
    if user.locked_until is not None:
        lu = user.locked_until
        if lu.tzinfo is None:
            lu = lu.replace(tzinfo=timezone.utc)
        if lu > datetime.now(timezone.utc):
            _log.warning(
                "Login failed: account under tier-1 lockout",
                extra={
                    "event_name": AuthEvent.LOGIN_FAILED,
                    "outcome_code": OutcomeCode.USER_LOCKED,
                    "ip": ip,
                    "email": email,
                    "user_id": user.id,
                    "locked_until": lu.isoformat(),
                },
            )
            record_auth_login_attempt(OutcomeCode.USER_LOCKED)
            await _persist_login_attempt(
                db,
                email=email,
                ip=ip,
                user_agent=ua,
                outcome=OutcomeCode.USER_LOCKED,
                user_id=user.id,
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account temporarily locked — please try again later",
            )

    if user.status == "locked":
        _log.warning(
            "Login failed: account is locked",
            extra={
                "event_name": AuthEvent.LOGIN_FAILED,
                "outcome_code": OutcomeCode.USER_LOCKED,
                "ip": ip,
                "email": email,
                "user_id": user.id,
            },
        )
        record_auth_login_attempt(OutcomeCode.USER_LOCKED)
        await _persist_login_attempt(
            db,
            email=email,
            ip=ip,
            user_agent=ua,
            outcome=OutcomeCode.USER_LOCKED,
            user_id=user.id,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is locked",
        )

    if user.status in ("deactivated", "deleted", "invited"):
        _log.warning(
            "Login failed: account is not active",
            extra={
                "event_name": AuthEvent.LOGIN_FAILED,
                "outcome_code": OutcomeCode.USER_INACTIVE,
                "ip": ip,
                "email": email,
                "user_id": user.id,
                "user_status": user.status,
            },
        )
        record_auth_login_attempt(OutcomeCode.USER_INACTIVE)
        await _persist_login_attempt(
            db,
            email=email,
            ip=ip,
            user_agent=ua,
            outcome=OutcomeCode.USER_INACTIVE,
            user_id=user.id,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is inactive",
        )

    # ── Password verification ─────────────────────────────────────────────────
    if not user.hashed_password or not verify_password(body.password, user.hashed_password):
        _log.warning(
            "Login failed: wrong password",
            extra={
                "event_name": AuthEvent.LOGIN_FAILED,
                "outcome_code": OutcomeCode.WRONG_PASSWORD,
                "ip": ip,
                "email": email,
                "user_id": user.id,
            },
        )
        record_auth_login_attempt(OutcomeCode.WRONG_PASSWORD)
        await _persist_login_attempt(
            db,
            email=email,
            ip=ip,
            user_agent=ua,
            outcome=OutcomeCode.WRONG_PASSWORD,
            user_id=user.id,
        )
        # SFBL-191: progressive lockout — increments counters and sets locked_until
        # / transitions status as required.  Must be called after _persist_login_attempt
        # is flushed so the attempt row counts are accurate.
        await db.flush()
        await handle_failed_attempt(db, user, ip=ip, username=email, user_agent=ua)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # ── Password OK — decide phase-1 vs phase-2 (SFBL-248) ────────────────────
    # Branch precedence (spec §2.2):
    #   (a) user_totp row exists → phase-1, must_enroll=false
    #   (b) no row + tenant require_2fa on → phase-1, must_enroll=true
    #   (c) otherwise → full phase-2 side effects
    totp_row = (
        await db.execute(select(UserTotp).where(UserTotp.user_id == user.id))
    ).scalar_one_or_none()

    require_2fa_on = False
    if totp_row is None:
        from app.services.settings.service import settings_service as _svc
        require_2fa_on = bool(
            await _svc.get("require_2fa") if _svc is not None
            else getattr(settings, "require_2fa", False)
        )

    if totp_row is not None or require_2fa_on:
        must_enroll = totp_row is None
        mfa_methods = ["enroll"] if must_enroll else ["totp", "backup_code"]
        _log.info(
            "MFA challenge issued",
            extra={
                "event_name": AuthEvent.LOGIN_MFA_CHALLENGE_ISSUED,
                "outcome_code": OutcomeCode.MFA_CHALLENGE_ISSUED,
                "ip": ip,
                "email": email,
                "user_id": user.id,
                "must_enroll": must_enroll,
            },
        )
        record_auth_login_attempt(OutcomeCode.MFA_CHALLENGE_ISSUED)
        await _persist_login_attempt(
            db,
            email=email,
            ip=ip,
            user_agent=ua,
            outcome=OutcomeCode.MFA_CHALLENGE_ISSUED,
            user_id=user.id,
        )
        await db.commit()
        return MfaRequiredResponse(
            mfa_required=True,
            mfa_token=create_mfa_token(user.id, must_enroll=must_enroll),
            mfa_methods=mfa_methods,
            must_enroll=must_enroll,
        )

    # ── Phase-2 success (no MFA configured) ───────────────────────────────────
    return await _login_success_phase2(db, user, ip=ip, ua=ua, email=email)


@router.get("/me", response_model=UserResponse)
async def me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Return the authenticated user's profile including RBAC permissions.

    Contract with SFBL-196 (frontend):
    - ``profile.name``: "admin" | "operator" | "viewer" | "desktop"
    - ``permissions``: sorted list of permission keys held by the user.
      In desktop mode (auth_mode=none) returns all keys and profile.name="desktop".

    SFBL-246: also returns an ``mfa`` sub-object describing the user's 2FA
    enrolment state. Desktop mode always reports not-enrolled (2FA does not
    apply when ``auth_mode=none`` — spec §0 D2).
    """
    from app.config import settings as _settings
    from app.auth.permissions import ALL_PERMISSION_KEYS

    # SFBL-251: surface the tenant-wide `require_2fa` setting so the UI can
    # render the forced-enrol / cannot-self-disable affordances.
    tenant_required = False
    if _settings.auth_mode != "none":
        try:
            from app.services.settings.service import settings_service as _svc  # noqa: PLC0415

            if _svc is not None:
                tenant_required = bool(await _svc.get("require_2fa"))
        except Exception:  # pragma: no cover - defensive
            tenant_required = bool(getattr(_settings, "require_2fa", False))

    no_mfa = MfaStatus(
        enrolled=False,
        enrolled_at=None,
        backup_codes_remaining=0,
        tenant_required=tenant_required,
    )

    # Build the base response from the ORM user, excluding the ORM profile
    # relationship (which is a Profile model, not ProfileSummary).
    base = UserResponse(
        id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
        status=current_user.status,
        is_active=current_user.is_active,
        profile=None,
        permissions=[],
        mfa=no_mfa,
    )

    if _settings.auth_mode == "none":
        # Desktop mode — virtual desktop user; no DB profile; 2FA not applicable.
        return base.model_copy(update={
            "profile": ProfileSummary(name="desktop"),
            "permissions": sorted(ALL_PERMISSION_KEYS),
            "mfa": no_mfa,
        })

    # Hosted mode — derive from the profile relationship (loaded via selectin)
    if current_user.profile is not None:
        profile_summary = ProfileSummary(name=current_user.profile.name)
        permission_list = sorted(current_user.profile.permission_keys)
    else:
        profile_summary = None
        permission_list = []

    # 2FA status lookup. Row existence ⇒ enrolled (per spec §0 D11).
    totp_row = (
        await db.execute(
            select(UserTotp).where(UserTotp.user_id == current_user.id)
        )
    ).scalar_one_or_none()

    if totp_row is None:
        mfa_status = no_mfa
    else:
        unconsumed = (
            await db.execute(
                select(func.count())
                .select_from(UserBackupCode)
                .where(
                    UserBackupCode.user_id == current_user.id,
                    UserBackupCode.consumed_at.is_(None),
                )
            )
        ).scalar_one()
        mfa_status = MfaStatus(
            enrolled=True,
            enrolled_at=totp_row.enrolled_at,
            backup_codes_remaining=int(unconsumed),
            tenant_required=tenant_required,
        )

    return base.model_copy(update={
        "profile": profile_summary,
        "permissions": permission_list,
        "mfa": mfa_status,
    })


@router.get("/config", response_model=AuthConfigResponse)
async def auth_config() -> AuthConfigResponse:
    return AuthConfigResponse(saml_enabled=False)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> None:
    """No-op — sessions are stateless JWT; client should discard the token."""
