"""Auth utilities: password hashing, JWT encode/decode, FastAPI dependencies."""

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.observability.events import AuthEvent, OutcomeCode

_bearer = HTTPBearer(auto_error=False)
_log = logging.getLogger(__name__)


# ── Password helpers ──────────────────────────────────────────────────────────

def _prehash(password: str) -> bytes:
    """SHA-256 prehash so passwords > 72 bytes are handled safely by bcrypt."""
    return base64.b64encode(hashlib.sha256(password.encode()).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(_prehash(plain), hashed.encode())


# ── JWT helpers ───────────────────────────────────────────────────────────────


def create_access_token(user: User, expiry_minutes: Optional[int] = None) -> str:
    """Create a signed JWT for *user*.

    Args:
        user: The authenticated User instance.
        expiry_minutes: Token lifetime in minutes.  When omitted, falls back to
            ``settings.jwt_expiry_minutes`` (the config default).  Callers in
            async contexts should resolve the DB-backed value via
            ``await settings_service.get("jwt_expiry_minutes")`` and pass it here.
    """
    _expiry = expiry_minutes if expiry_minutes is not None else settings.jwt_expiry_minutes
    now = datetime.now(tz=timezone.utc)
    exp = int(now.timestamp()) + _expiry * 60
    payload = {
        "sub": user.id,
        "email": user.email,
        "iat": int(now.timestamp()),
        "exp": exp,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_mfa_token(
    user_id: str, *, must_enroll: bool, ttl_minutes: int = 5
) -> str:
    """Create a short-TTL pre-auth token that only unlocks the ``/login/2fa*`` routes.

    The token carries ``mfa_pending=true`` so ``get_current_user`` rejects it
    (see SFBL-247). Only :func:`get_mfa_pending_user` accepts it, and only
    when both ``mfa_pending`` and the expected ``must_enroll`` flag match.
    """
    now = datetime.now(tz=timezone.utc)
    exp = int(now.timestamp()) + ttl_minutes * 60
    payload = {
        "sub": user_id,
        "mfa_pending": True,
        "must_enroll": bool(must_enroll),
        "purpose": "mfa_challenge",
        "iat": int(now.timestamp()),
        "exp": exp,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Raises HTTP 401 on any failure."""
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ── FastAPI dependencies ──────────────────────────────────────────────────────


_DESKTOP_USER = User(id="desktop", email="desktop@localhost", status="active", is_admin=True)

# ── PAT authentication constants ──────────────────────────────────────────────

#: Only update ``last_used_at`` if the token has not been used within this window.
#: This throttles DB writes on high-frequency callers without losing activity
#: visibility — last_used_at will never be stale by more than this duration.
_LAST_USED_THROTTLE_SECONDS = 300  # 5 minutes


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
    request: Request = None,  # FastAPI special-cases Request; None default allows direct calls in tests
) -> User:
    if settings.auth_mode == "none":
        return _DESKTOP_USER

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── PAT branch: deterministic check BEFORE decode_access_token ───────────
    # Must be checked before attempting JWT decode so that a PAT bearer value
    # (which is NOT a valid JWT) does not trigger a JWTError 401 on the JWT path.
    from app.services.pat import TOKEN_PREFIX, hash_token as _hash_pat  # lazy import to break circular dep

    if credentials.credentials.startswith(TOKEN_PREFIX):
        return await _authenticate_pat(credentials.credentials, db, request)

    # ── JWT path ──────────────────────────────────────────────────────────────
    payload = decode_access_token(credentials.credentials)
    user_id: Optional[str] = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # SFBL-247/248: reject tokens carrying the ``mfa_pending`` step-up marker
    # from general-purpose endpoints. Only the ``/api/auth/2fa/*`` enrol and
    # challenge routes accept these (via ``get_mfa_pending_user``).
    if payload.get("mfa_pending") is True:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Two-factor authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Gate on status — only 'active' accounts may authenticate.
    if user.status != "active":
        _log.warning(
            "Token rejected: user status is not active",
            extra={
                "event_name": AuthEvent.TOKEN_REJECTED,
                "outcome_code": OutcomeCode.USER_INACTIVE,
                "user_id": user_id,
                "user_status": user.status,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Gate on tier-1 auto-lockout — locked_until is set when the threshold is
    # breached and clears automatically once the window passes.
    if user.locked_until is not None:
        lu = user.locked_until
        if lu.tzinfo is None:
            lu = lu.replace(tzinfo=timezone.utc)
        if lu > datetime.now(timezone.utc):
            _log.warning(
                "Token rejected: account under tier-1 lockout",
                extra={
                    "event_name": AuthEvent.TOKEN_REJECTED,
                    "outcome_code": OutcomeCode.USER_INACTIVE,
                    "user_id": user_id,
                    "locked_until": lu.isoformat(),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account temporarily locked — please try again later",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # JWT invalidation watermark — reject tokens issued before the last
    # password (or email) change.  ``iat`` is an integer Unix timestamp;
    # password_changed_at is timezone-aware UTC stored in the DB.
    if user.password_changed_at is not None:
        token_iat: Optional[int] = payload.get("iat")
        if token_iat is not None:
            # Ensure password_changed_at is UTC-aware for comparison
            pca = user.password_changed_at
            if pca.tzinfo is None:
                pca = pca.replace(tzinfo=timezone.utc)
            pca_ts = pca.timestamp()
            if token_iat < pca_ts:
                _log.warning(
                    "Token rejected: issued before password change",
                    extra={
                        "event_name": AuthEvent.TOKEN_REJECTED,
                        "outcome_code": OutcomeCode.STALE_AFTER_PASSWORD_CHANGE,
                        "user_id": user_id,
                        "token_iat": token_iat,
                        "password_changed_at_ts": pca_ts,
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token invalidated by password change — please log in again",
                    headers={"WWW-Authenticate": "Bearer"},
                )

    if request is not None:
        request.state.auth_method = "session"

    return user


async def _authenticate_pat(
    plaintext: str,
    db: AsyncSession,
    request: Optional[Request],
) -> User:
    """Validate a PAT bearer token and return its owning User.

    Called from ``get_current_user`` when the bearer value starts with
    TOKEN_PREFIX.  Performs an indexed hash lookup, constant-time comparison,
    revocation + expiry gates, owner load with profile eager-loaded (blocker for
    require_permission), account-status + lockout gates, write-throttled
    last_used_at update, and observability emission.

    Design notes
    ~~~~~~~~~~~~
    - ``password_changed_at`` does NOT revoke PATs — PATs are long-lived
      credentials intentionally decoupled from interactive sessions.
    - ``mfa_pending`` does not apply to PATs — PATs are opaque and carry no
      step-up state.  No action needed here; the gate would be unreachable.
    - ``last_used_at`` is updated at most once per ``_LAST_USED_THROTTLE_SECONDS``
      window (last-writer-wins under concurrency — this is acceptable; the column
      is informational only).
    - Token material is never logged (honours sanitization.py rules).
    """
    from app.models.personal_access_token import PersonalAccessToken
    from app.services.pat import hash_token as _hash_pat

    # Compute hash and look up by index (O(1) unique-index lookup).
    token_hash = _hash_pat(plaintext)

    result = await db.execute(
        select(PersonalAccessToken).where(PersonalAccessToken.token_hash == token_hash)
    )
    token_row = result.scalar_one_or_none()

    if token_row is None or not hmac.compare_digest(token_row.token_hash, token_hash):
        # Unknown token — do not reveal whether the prefix matched a real row.
        _log.warning(
            "PAT rejected: token not found",
            extra={
                "event_name": AuthEvent.TOKEN_REJECTED,
                "outcome_code": OutcomeCode.INVALID_TOKEN,
                "rejection_reason": "pat_unknown",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if token_row.revoked_at is not None:
        _log.warning(
            "PAT rejected: token has been revoked",
            extra={
                "event_name": AuthEvent.TOKEN_REJECTED,
                "outcome_code": OutcomeCode.INVALID_TOKEN,
                "rejection_reason": "pat_revoked",
                "pat_id": token_row.id,
                "user_id": token_row.user_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if token_row.expires_at is not None:
        exp = token_row.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= datetime.now(timezone.utc):
            _log.warning(
                "PAT rejected: token has expired",
                extra={
                    "event_name": AuthEvent.TOKEN_REJECTED,
                    "outcome_code": OutcomeCode.EXPIRED_TOKEN,
                    "rejection_reason": "pat_expired",
                    "pat_id": token_row.id,
                    "user_id": token_row.user_id,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # Eager-load the owner WITH profile in the same session.
    # ``db.get`` does NOT selectin-load related objects when the row is already
    # in the identity map, so we use a fresh select with selectinload(User.profile)
    # to guarantee the profile (and its permission_keys) are available.
    # Without this, require_permission() sees profile=None and returns 403.
    owner_result = await db.execute(
        select(User).options(selectinload(User.profile)).where(User.id == token_row.user_id)
    )
    user = owner_result.scalar_one_or_none()

    if user is None:
        _log.warning(
            "PAT rejected: owning user not found",
            extra={
                "event_name": AuthEvent.TOKEN_REJECTED,
                "outcome_code": OutcomeCode.USER_INACTIVE,
                "rejection_reason": "pat_unknown",
                "pat_id": token_row.id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Re-apply the same status + lockout gates as the JWT path.
    # password_changed_at does NOT revoke PATs (decision — PATs are long-lived
    # credentials intentionally decoupled from interactive session watermarks).
    if user.status != "active":
        _log.warning(
            "PAT rejected: user status is not active",
            extra={
                "event_name": AuthEvent.TOKEN_REJECTED,
                "outcome_code": OutcomeCode.USER_INACTIVE,
                "rejection_reason": "pat_user_inactive",
                "user_id": user.id,
                "user_status": user.status,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.locked_until is not None:
        lu = user.locked_until
        if lu.tzinfo is None:
            lu = lu.replace(tzinfo=timezone.utc)
        if lu > datetime.now(timezone.utc):
            _log.warning(
                "PAT rejected: account under tier-1 lockout",
                extra={
                    "event_name": AuthEvent.TOKEN_REJECTED,
                    "outcome_code": OutcomeCode.USER_INACTIVE,
                    "rejection_reason": "pat_account_locked",
                    "user_id": user.id,
                    "locked_until": lu.isoformat(),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account temporarily locked — please try again later",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # Write-throttled last_used_at update — update only if None or older than
    # _LAST_USED_THROTTLE_SECONDS.  Last-writer-wins under concurrency is
    # acceptable; the column is informational, not a security gate.
    now = datetime.now(timezone.utc)
    should_update = token_row.last_used_at is None
    if not should_update and token_row.last_used_at is not None:
        luat = token_row.last_used_at
        if luat.tzinfo is None:
            luat = luat.replace(tzinfo=timezone.utc)
        should_update = (now - luat) >= timedelta(seconds=_LAST_USED_THROTTLE_SECONDS)

    if should_update:
        token_row.last_used_at = now
        db.add(token_row)
        await db.commit()

    # Observability — emit PAT_USED; never log token material.
    _log.info(
        "PAT authenticated (user=%s, pat_id=%s)",
        user.id,
        token_row.id,
        extra={
            "event_name": AuthEvent.PAT_USED,
            "outcome_code": OutcomeCode.SUCCESS,
            "user_id": user.id,
            "pat_id": token_row.id,
            # last4 is safe — cannot be used to reconstruct the token.
            "pat_last4": token_row.last4,
        },
    )

    if request is not None:
        request.state.auth_method = "pat"

    return user


async def get_mfa_pending_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> tuple[User, bool]:
    """Dependency used exclusively by ``/api/auth/login/2fa*`` routes.

    Returns ``(user, must_enroll)``. Rejects any bearer token that isn't
    a valid ``mfa_pending`` token or whose user is not permitted to log in
    (inactive / locked). Per spec §4.2, this is the only dependency that
    accepts ``mfa_pending=true`` tokens.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "mfa_token_invalid",
                "message": "MFA challenge token is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "mfa_token_invalid",
                "message": "MFA challenge token is invalid or expired.",
            },
        ) from exc

    if payload.get("mfa_pending") is not True:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "mfa_token_invalid",
                "message": "MFA challenge token is invalid.",
            },
        )

    user_id: Optional[str] = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "mfa_token_invalid",
                "message": "MFA challenge token is invalid.",
            },
        )

    user = await db.get(User, user_id)
    if user is None or user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "mfa_token_invalid",
                "message": "MFA challenge token is invalid.",
            },
        )

    # Mirror the tier-1 lockout gate in ``get_current_user`` — without this,
    # a user locked by ``handle_failed_attempt`` after receiving a valid
    # ``mfa_token`` could still complete login and bypass the lockout window.
    if user.locked_until is not None:
        lu = user.locked_until
        if lu.tzinfo is None:
            lu = lu.replace(tzinfo=timezone.utc)
        if lu > datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "account_locked",
                    "message": "Account temporarily locked — please try again later.",
                    "locked_until": lu.isoformat(),
                },
            )

    return user, bool(payload.get("must_enroll", False))


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency that requires the authenticated user to have is_admin=True.

    Raises HTTP 403 if the user is authenticated but is not an admin.
    In desktop profile (auth_mode=none) the injected _DESKTOP_USER already
    has is_admin=True so this dependency is always satisfied.

    # DEPRECATED: use require_permission(...) from app.auth.permissions instead.
    # Removal tracked as a follow-up cleanup ticket (post SFBL-195).
    # Kept as a shim so existing callsites and tests continue to pass.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


# ── Password policy ───────────────────────────────────────────────────────────


class PasswordPolicyError(ValueError):
    """Raised when a password does not satisfy the minimum complexity rules.

    Inherits from ValueError for backward compatibility with callers that
    catch ValueError.  The ``failures`` attribute lists each unmet rule
    description so callers can present structured feedback.
    """

    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        super().__init__(
            "Password does not meet minimum requirements: "
            + ", ".join(failures)
            + "."
        )


_PASSWORD_MIN_LENGTH = 12
_PASSWORD_RULES: tuple[tuple, ...] = (
    (lambda p: len(p) >= _PASSWORD_MIN_LENGTH, f"at least {_PASSWORD_MIN_LENGTH} characters"),
    (lambda p: any(c.isupper() for c in p), "at least one uppercase letter"),
    (lambda p: any(c.islower() for c in p), "at least one lowercase letter"),
    (lambda p: any(c.isdigit() for c in p), "at least one digit"),
    (lambda p: any(not c.isalnum() for c in p), "at least one special character"),
)


def validate_password_strength(password: str) -> None:
    """Validate that *password* meets the minimum complexity policy.

    Raises :class:`PasswordPolicyError` (a :class:`ValueError` subclass)
    listing every unmet requirement if the password is too weak.

    Rules (must ALL be satisfied):
    - At least 12 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special (non-alphanumeric) character
    """
    failures = [desc for check, desc in _PASSWORD_RULES if not check(password)]
    if failures:
        raise PasswordPolicyError(failures)


def _validate_password_strength(password: str) -> None:
    """Internal alias kept for seed_admin compatibility.

    Wraps :func:`validate_password_strength` and re-raises the error with
    the ``ADMIN_PASSWORD`` prefix that existing startup code and tests expect.
    """
    try:
        validate_password_strength(password)
    except PasswordPolicyError as exc:
        raise ValueError(
            "ADMIN_PASSWORD does not meet minimum requirements: "
            + ", ".join(exc.failures)
            + "."
        ) from exc


# ── WebSocket helper ──────────────────────────────────────────────────────────


async def seed_admin(db: AsyncSession) -> None:
    """Bootstrap the first admin user if no users exist.

    SFBL-198: email is now the identity column. Boot behaviour:
    - No users → seed from ADMIN_EMAIL + ADMIN_PASSWORD (ADMIN_USERNAME used as display_name).
    - Existing admin with no email + ADMIN_EMAIL set → backfill email and proceed.
    - Existing admin with no email + ADMIN_EMAIL unset → RuntimeError with guidance.
    - Existing admin has email → ignore ADMIN_EMAIL (only used at first seed / backfill).

    Skipped entirely in desktop profile (auth_mode=none) — no managed users needed.
    """
    if settings.auth_mode == "none":
        return

    count = await db.scalar(select(func.count()).select_from(User))

    # ── Existing-install backfill (admin row exists but has no email) ──────────
    if count and count > 0:
        # Upgrade path: pre-SFBL-198 installs where the admin row may be missing
        # its email (migration 0023 has a pre-flight that aborts in this case
        # to give operators a chance to run with ADMIN_EMAIL set so this branch
        # can patch the row before the migration runs).
        admin: User | None = await db.scalar(
            select(User).where(User.is_admin.is_(True)).limit(1)  # type: ignore[attr-defined]
        )
        if admin is None or admin.email:
            # Nothing to do — no admin, or admin already has an email.
            return
        configured_email = settings.admin_email
        if not configured_email:
            raise RuntimeError(
                "Existing admin user has no email and ADMIN_EMAIL is unset. "
                "Set ADMIN_EMAIL in the environment and restart so the admin "
                "row can be backfilled before migration 0023 enforces NOT NULL."
            )
        admin.email = configured_email
        await db.commit()
        _log.warning(
            "Backfilled admin email on startup",
            extra={"user_id": admin.id, "email": configured_email},
        )
        return

    # ── First boot — seed the admin account ───────────────────────────────────
    email = settings.admin_email
    password = settings.admin_password
    if not email or not password:
        raise RuntimeError(
            "No users found in the database. "
            "Set ADMIN_EMAIL and ADMIN_PASSWORD environment variables "
            "to seed the initial admin account on first boot."
        )

    try:
        _validate_password_strength(password)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    # Look up the seeded admin profile (from migration 0021) so the new user
    # satisfies the NOT NULL profile_id constraint (migration 0022).
    from app.models.profile import Profile  # noqa: PLC0415

    admin_profile_id = await db.scalar(
        select(Profile.id).where(Profile.name == "admin")
    )
    if admin_profile_id is None:
        raise RuntimeError(
            "Admin profile not found. Ensure profile-seed migrations have run "
            "before seed_admin."
        )

    # ADMIN_USERNAME, if set, is used as the display_name.
    admin = User(
        email=email,
        display_name=settings.admin_username or None,
        hashed_password=hash_password(password),
        is_admin=True,
        status="active",
        profile_id=admin_profile_id,
    )
    db.add(admin)
    await db.commit()


def validate_ws_token(token: Optional[str]) -> dict:
    """Validate a raw JWT string from a WebSocket query parameter.

    Usage: ``payload = validate_ws_token(request.query_params.get("token"))``
    Raises HTTP 401 if the token is missing or invalid.

    In desktop profile (auth_mode=none) the token is not required and validation
    is skipped entirely.
    """
    if settings.auth_mode == "none":
        return {}
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return decode_access_token(token)
