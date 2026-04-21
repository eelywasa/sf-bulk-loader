"""Auth utilities: password hashing, JWT encode/decode, FastAPI dependencies."""

import base64
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select

import bcrypt
from fastapi import Depends, HTTPException, status
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
        "username": user.username,
        "role": user.role,
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


_DESKTOP_USER = User(id="desktop", username="desktop", status="active", is_admin=True)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if settings.auth_mode == "none":
        return _DESKTOP_USER

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(credentials.credentials)
    user_id: Optional[str] = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
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

    return user


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

    Idempotent — does nothing when at least one user is already present.
    Raises RuntimeError on first boot if the required env vars are absent or
    if ADMIN_PASSWORD does not meet minimum complexity requirements.

    Skipped entirely in desktop profile (auth_mode=none) — no managed users needed.
    """
    if settings.auth_mode == "none":
        return

    count = await db.scalar(select(func.count()).select_from(User))
    if count and count > 0:
        return

    username = settings.admin_username
    password = settings.admin_password
    if not username or not password:
        raise RuntimeError(
            "No users found in the database. "
            "Set ADMIN_USERNAME and ADMIN_PASSWORD environment variables "
            "to seed the initial admin account on first boot."
        )

    try:
        _validate_password_strength(password)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    admin = User(
        username=username,
        hashed_password=hash_password(password),
        is_admin=True,
        status="active",
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
