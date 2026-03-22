"""Auth utilities: password hashing, JWT encode/decode, FastAPI dependencies."""

import base64
import hashlib
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

_bearer = HTTPBearer(auto_error=False)


# ── Password helpers ──────────────────────────────────────────────────────────

def _prehash(password: str) -> bytes:
    """SHA-256 prehash so passwords > 72 bytes are handled safely by bcrypt."""
    return base64.b64encode(hashlib.sha256(password.encode()).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(_prehash(plain), hashed.encode())


# ── JWT helpers ───────────────────────────────────────────────────────────────


def create_access_token(user: User) -> str:
    now = datetime.now(tz=timezone.utc)
    exp = int(now.timestamp()) + settings.jwt_expiry_minutes * 60
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


_DESKTOP_USER = User(id="desktop", username="desktop", role="admin", is_active=True)


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
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ── WebSocket helper ──────────────────────────────────────────────────────────


_PASSWORD_MIN_LENGTH = 12
_PASSWORD_RULES = (
    (lambda p: len(p) >= _PASSWORD_MIN_LENGTH, f"at least {_PASSWORD_MIN_LENGTH} characters"),
    (lambda p: any(c.isupper() for c in p), "at least one uppercase letter"),
    (lambda p: any(c.islower() for c in p), "at least one lowercase letter"),
    (lambda p: any(c.isdigit() for c in p), "at least one digit"),
    (lambda p: any(not c.isalnum() for c in p), "at least one special character"),
)


def _validate_password_strength(password: str) -> None:
    """Raise ValueError listing all unmet password requirements."""
    failures = [desc for check, desc in _PASSWORD_RULES if not check(password)]
    if failures:
        raise ValueError(
            "ADMIN_PASSWORD does not meet minimum requirements: "
            + ", ".join(failures)
            + "."
        )


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
        role="admin",
        is_active=True,
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
