"""Auth API — login, session inspection, and auth configuration."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.observability.events import AuthEvent, OutcomeCode
from app.observability.metrics import record_auth_login_attempt
from app.schemas.auth import AuthConfigResponse, LoginRequest, ProfileSummary, TokenResponse, UserResponse
from app.services.auth import create_access_token, get_current_user, verify_password
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
    username: str,
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
                username=username,
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
                "username": username,
                "ip": ip,
            },
        )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Authenticate with username + password and return a JWT.

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
    username = body.username

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
                "username": username,
            },
        )
        record_auth_login_attempt(OutcomeCode.IP_LIMIT)
        await _persist_login_attempt(
            db,
            username=username,
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
    result = await db.execute(select(User).where(User.username == username))
    user: User | None = result.scalars().first()

    if user is None:
        # Unknown username — persist with user_id=null
        _log.warning(
            "Login failed: unknown user",
            extra={
                "event_name": AuthEvent.LOGIN_FAILED,
                "outcome_code": OutcomeCode.UNKNOWN_USER,
                "ip": ip,
                "username": username,
            },
        )
        record_auth_login_attempt(OutcomeCode.UNKNOWN_USER)
        await _persist_login_attempt(
            db,
            username=username,
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
                    "username": username,
                    "user_id": user.id,
                    "locked_until": lu.isoformat(),
                },
            )
            record_auth_login_attempt(OutcomeCode.USER_LOCKED)
            await _persist_login_attempt(
                db,
                username=username,
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
                "username": username,
                "user_id": user.id,
            },
        )
        record_auth_login_attempt(OutcomeCode.USER_LOCKED)
        await _persist_login_attempt(
            db,
            username=username,
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
                "username": username,
                "user_id": user.id,
                "user_status": user.status,
            },
        )
        record_auth_login_attempt(OutcomeCode.USER_INACTIVE)
        await _persist_login_attempt(
            db,
            username=username,
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
                "username": username,
                "user_id": user.id,
            },
        )
        record_auth_login_attempt(OutcomeCode.WRONG_PASSWORD)
        await _persist_login_attempt(
            db,
            username=username,
            ip=ip,
            user_agent=ua,
            outcome=OutcomeCode.WRONG_PASSWORD,
            user_id=user.id,
        )
        # SFBL-191: progressive lockout — increments counters and sets locked_until
        # / transitions status as required.  Must be called after _persist_login_attempt
        # is flushed so the attempt row counts are accurate.
        await db.flush()
        await handle_failed_attempt(db, user, ip=ip, username=username, user_agent=ua)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # ── Successful authentication ─────────────────────────────────────────────
    must_reset = user.must_reset_password

    if must_reset:
        outcome_code = OutcomeCode.MUST_RESET_PASSWORD
        _log.info(
            "Login succeeded (must reset password)",
            extra={
                "event_name": AuthEvent.LOGIN_SUCCEEDED,
                "outcome_code": outcome_code,
                "ip": ip,
                "username": username,
                "user_id": user.id,
            },
        )
    else:
        outcome_code = OutcomeCode.OK
        _log.info(
            "Login succeeded",
            extra={
                "event_name": AuthEvent.LOGIN_SUCCEEDED,
                "outcome_code": outcome_code,
                "ip": ip,
                "username": username,
                "user_id": user.id,
            },
        )

    # SFBL-191: reset lockout counters on successful authentication
    await handle_successful_login(db, user)

    record_auth_login_attempt(outcome_code)
    await _persist_login_attempt(
        db,
        username=username,
        ip=ip,
        user_agent=ua,
        outcome=outcome_code,
        user_id=user.id,
    )
    await db.commit()

    # Read JWT expiry from DB-backed settings (SFBL-156).
    from app.services.settings.service import settings_service as _svc
    _jwt_expiry: int = (
        await _svc.get("jwt_expiry_minutes") if _svc is not None
        else settings.jwt_expiry_minutes
    )
    return TokenResponse(
        access_token=create_access_token(user, expiry_minutes=_jwt_expiry),
        expires_in=_jwt_expiry * 60,
        must_reset_password=must_reset,
    )


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """Return the authenticated user's profile including RBAC permissions.

    Contract with SFBL-196 (frontend):
    - ``profile.name``: "admin" | "operator" | "viewer" | "desktop"
    - ``permissions``: sorted list of permission keys held by the user.
      In desktop mode (auth_mode=none) returns all keys and profile.name="desktop".
    """
    from app.config import settings as _settings
    from app.auth.permissions import ALL_PERMISSION_KEYS

    # Build the base response from the ORM user, excluding the ORM profile
    # relationship (which is a Profile model, not ProfileSummary).
    base = UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        display_name=current_user.display_name,
        role=current_user.role,
        status=current_user.status,
        is_active=current_user.is_active,
        profile=None,
        permissions=[],
    )

    if _settings.auth_mode == "none":
        # Desktop mode — virtual desktop user; no DB profile
        return base.model_copy(update={
            "profile": ProfileSummary(name="desktop"),
            "permissions": sorted(ALL_PERMISSION_KEYS),
        })

    # Hosted mode — derive from the profile relationship (loaded via selectin)
    if current_user.profile is not None:
        profile_summary = ProfileSummary(name=current_user.profile.name)
        permission_list = sorted(current_user.profile.permission_keys)
    else:
        profile_summary = None
        permission_list = []

    return base.model_copy(update={
        "profile": profile_summary,
        "permissions": permission_list,
    })


@router.get("/config", response_model=AuthConfigResponse)
async def auth_config() -> AuthConfigResponse:
    return AuthConfigResponse(saml_enabled=False)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> None:
    """No-op — sessions are stateless JWT; client should discard the token."""
