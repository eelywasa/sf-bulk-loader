"""PAT management API — /api/me/tokens (SFBL-368).

Endpoints for the authenticated user to issue, list, and revoke their own
Personal Access Tokens.

Security model
--------------
- ``GET /api/me/tokens``   — list own PATs (metadata only). Requires tokens.manage.
- ``POST /api/me/tokens``  — issue a new PAT.  Requires tokens.manage AND session auth.
- ``DELETE /api/me/tokens/{id}`` — revoke a PAT.  Requires tokens.manage AND session auth.

The session-auth restriction on POST / DELETE means a leaked PAT cannot be used
to mint new tokens or revoke existing ones.  ``require_session_auth`` (defined in
app.auth.permissions) reads ``request.state.auth_method`` set by SFBL-367.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import TOKENS_MANAGE, require_permission, require_session_auth
from app.database import get_db
from app.models.personal_access_token import PersonalAccessToken
from app.models.user import User
from app.schemas.pat import PatCreate, PatCreateResponse, PatMetadata
from app.services import pat as pat_service

router = APIRouter(prefix="/api/me/tokens", tags=["me"])

_log = logging.getLogger(__name__)


@router.get("", response_model=list[PatMetadata])
async def list_tokens(
    current_user: User = Depends(require_permission(TOKENS_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> list[PersonalAccessToken]:
    """List the authenticated user's PATs (metadata only — no plaintext or hash).

    Returns all tokens for the caller, ordered newest-first.  Revoked tokens
    are included so users can audit their token history.
    """
    result = await db.execute(
        select(PersonalAccessToken)
        .where(PersonalAccessToken.user_id == current_user.id)
        .order_by(PersonalAccessToken.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("", response_model=PatCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_token(
    body: PatCreate,
    current_user: User = Depends(require_session_auth),
    db: AsyncSession = Depends(get_db),
) -> PatCreateResponse:
    """Issue a new PAT for the authenticated user.

    The plaintext token is returned **exactly once** in this response.  It
    cannot be recovered after this call — store it securely immediately.

    Requires session (cookie/JWT) authentication — PAT-authenticated requests
    are rejected to prevent a leaked PAT from minting new tokens.

    Also requires the ``tokens.manage`` permission.
    """
    # require_session_auth already calls get_current_user, but we still need
    # the tokens.manage permission check.  We do it here inline so the session
    # check (403 "session_required") fires before the permission check (403
    # "permission_denied"), which gives a clearer error for PAT callers.
    from app.config import settings as _settings

    if _settings.auth_mode != "none":
        profile = current_user.profile
        if profile is None or TOKENS_MANAGE not in profile.permission_keys:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "permission_denied",
                    "required_permission": TOKENS_MANAGE,
                },
            )

    pat, plaintext = await pat_service.issue(
        db,
        current_user,
        name=body.name,
        expires_at=body.expires_at,
    )
    await db.commit()
    await db.refresh(pat)

    return PatCreateResponse(
        id=pat.id,
        name=pat.name,
        prefix=pat.prefix,
        last4=pat.last4,
        created_at=pat.created_at,
        last_used_at=pat.last_used_at,
        expires_at=pat.expires_at,
        revoked_at=pat.revoked_at,
        token=plaintext,
    )


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: str,
    current_user: User = Depends(require_session_auth),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke a PAT owned by the authenticated user.

    Returns 404 if the token does not exist.
    Returns 403 if the token belongs to a different user.
    Idempotent: revoking an already-revoked token is a no-op (204 returned).

    Requires session (cookie/JWT) authentication and the ``tokens.manage``
    permission.
    """
    from app.config import settings as _settings

    if _settings.auth_mode != "none":
        profile = current_user.profile
        if profile is None or TOKENS_MANAGE not in profile.permission_keys:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "permission_denied",
                    "required_permission": TOKENS_MANAGE,
                },
            )

    token = await db.get(PersonalAccessToken, token_id)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )
    if token.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorised for this token",
        )

    await pat_service.revoke(db, token)
    await db.commit()
