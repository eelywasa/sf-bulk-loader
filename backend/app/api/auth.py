"""Auth API — login, session inspection, and auth configuration."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.auth import AuthConfigResponse, LoginRequest, TokenResponse, UserResponse
from app.services.auth import create_access_token, get_current_user, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    result = await db.execute(select(User).where(User.username == body.username))
    user: User | None = result.scalars().first()
    if user is None or not user.hashed_password or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive")
    return TokenResponse(
        access_token=create_access_token(user),
        expires_in=settings.jwt_expiry_minutes * 60,
    )


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.get("/config", response_model=AuthConfigResponse)
async def auth_config() -> AuthConfigResponse:
    return AuthConfigResponse(saml_enabled=False)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> None:
    """No-op — sessions are stateless JWT; client should discard the token."""
