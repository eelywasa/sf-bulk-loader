"""Admin users API — full user lifecycle management (SFBL-200).

All endpoints are under ``/api/admin/users`` and require the ``users.manage``
permission (profile-based RBAC, SFBL-195).  The ``require_admin`` helper in
``app.services.auth`` is still used for backward compatibility on the legacy
unlock endpoint that predates the permission model.

Endpoints
---------
GET    /api/admin/users                   — paginated list (status filter, deleted opt-in)
POST   /api/admin/users                   — invite user (returns raw token for SFBL-202)
GET    /api/admin/users/{id}              — user detail
PUT    /api/admin/users/{id}              — update profile / display_name
POST   /api/admin/users/{id}/unlock       — clear lockout (SFBL-191, kept)
POST   /api/admin/users/{id}/deactivate   — active → deactivated
POST   /api/admin/users/{id}/reactivate   — deactivated → active
POST   /api/admin/users/{id}/reset-password — issue temp password
POST   /api/admin/users/{id}/resend-invite  — new InvitationToken for invited user
DELETE /api/admin/users/{id}              — soft-delete (status='deleted')

Anti-bricking guard
-------------------
Any operation that would leave **zero** users with the system ``admin`` profile
is rejected with 409.  This prevents accidental lock-out of all administrators.
The bootstrap admin (``settings.admin_email``) additionally cannot be deleted.

Observability
-------------
Emits ``AuthEvent`` log events and increments ``auth_invitations_total`` per the
SFBL-200 spec (§8.4).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import USERS_MANAGE, require_permission
from app.config import settings
from app.database import get_db
from app.models.invitation_token import InvitationToken
from app.models.login_attempt import LoginAttempt
from app.models.profile import Profile
from app.models.user import User
from app.observability.events import AuthEvent, OutcomeCode
from app.observability.metrics import record_account_unlocked, record_auth_invitation
from app.schemas.admin_users import (
    AdminResetPasswordResponse,
    AdminUserListResponse,
    AdminUserResponse,
    InviteUserRequest,
    InviteUserResponse,
    ResendInviteResponse,
    UpdateUserRequest,
)
from app.schemas.auth import UserResponse
from app.services.auth import hash_password

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])

_log = logging.getLogger(__name__)

# ── Shared dependency ─────────────────────────────────────────────────────────

_UsersManageUser = Annotated[User, Depends(require_permission(USERS_MANAGE))]


# ── Internal helpers ──────────────────────────────────────────────────────────


async def _get_user_or_404(user_id: str, db: AsyncSession) -> User:
    user: User | None = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


async def _get_admin_profile(db: AsyncSession) -> Profile | None:
    """Return the system 'admin' profile row, or None if not found."""
    result = await db.execute(select(Profile).where(Profile.name == "admin"))
    return result.scalar_one_or_none()


async def _count_active_admins(db: AsyncSession, admin_profile_id: str) -> int:
    """Count users with the admin profile whose status is active."""
    result = await db.execute(
        select(func.count(User.id)).where(
            User.profile_id == admin_profile_id,
            User.status.in_(["active", "invited"]),
        )
    )
    return result.scalar_one()


async def _guard_last_admin(
    target: User,
    db: AsyncSession,
    operation: str,
) -> None:
    """Raise 409 if the operation would eliminate all admin-profile users.

    Only runs when the target currently holds the admin profile.
    """
    admin_profile = await _get_admin_profile(db)
    if admin_profile is None:
        return  # No admin profile configured — no guard needed
    if target.profile_id != admin_profile.id:
        return  # Target is not an admin — guard is irrelevant

    remaining = await _count_active_admins(db, admin_profile.id)
    # After the pending operation there would be (remaining - 1) active admins.
    if remaining <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "last_admin_guard",
                "message": (
                    f"Cannot {operation} the last active administrator. "
                    "Assign the admin profile to another user first."
                ),
            },
        )


# ── List users ────────────────────────────────────────────────────────────────


@router.get("", response_model=AdminUserListResponse, summary="List users (admin)")
@router.get("/", response_model=AdminUserListResponse, include_in_schema=False)
async def list_users(
    current_user: _UsersManageUser,
    db: AsyncSession = Depends(get_db),
    status_filter: Optional[str] = Query(None, alias="status"),
    include_deleted: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> AdminUserListResponse:
    """Return a paginated list of users.

    - Deleted users are excluded by default; pass ``?include_deleted=true`` to
      include tombstoned rows.
    - Filter by status with ``?status=active|invited|deactivated|locked``.
    """
    q = select(User)
    if not include_deleted:
        q = q.where(User.status != "deleted")
    if status_filter is not None:
        q = q.where(User.status == status_filter)

    # Count
    count_q = select(func.count()).select_from(q.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    # Page
    q = q.order_by(User.created_at).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()

    return AdminUserListResponse(
        items=[AdminUserResponse.from_user(u) for u in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# ── Get user ──────────────────────────────────────────────────────────────────


@router.get("/{user_id}", response_model=AdminUserResponse, summary="Get user detail (admin)")
async def get_user(
    user_id: str,
    current_user: _UsersManageUser,
    db: AsyncSession = Depends(get_db),
) -> AdminUserResponse:
    user = await _get_user_or_404(user_id, db)
    return AdminUserResponse.from_user(user)


# ── Invite user ───────────────────────────────────────────────────────────────


@router.post("", response_model=InviteUserResponse, status_code=status.HTTP_201_CREATED, summary="Invite user (admin)")
@router.post("/", response_model=InviteUserResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def invite_user(
    body: InviteUserRequest,
    current_user: _UsersManageUser,
    db: AsyncSession = Depends(get_db),
) -> InviteUserResponse:
    """Create a pending user row and an invitation token.

    The raw token is returned once in this response so SFBL-202 can construct
    the accept URL and deliver it by email.  Only the SHA-256 hash is stored.

    If the email is already in use, 409 is returned immediately.
    """
    # Check email uniqueness
    existing = (
        await db.execute(select(User).where(User.email == body.email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "email_in_use", "message": "Email address is already registered."},
        )

    # Validate profile
    profile: Profile | None = await db.get(Profile, body.profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_profile_id", "message": "Profile not found."},
        )

    now = datetime.now(timezone.utc)

    # Verify inviter exists in the DB before setting the FK (self-referential FK).
    # In tests, the current_user may be a synthetic object not persisted to the DB.
    inviter_in_db = await db.get(User, current_user.id)
    invited_by_id: str | None = current_user.id if inviter_in_db is not None else None

    # Create pending user
    new_user = User(
        id=str(uuid.uuid4()),
        email=body.email,
        display_name=body.display_name,
        status="invited",
        profile_id=body.profile_id,
        invited_by=invited_by_id,
        invited_at=now,
    )
    db.add(new_user)
    await db.flush()  # populate new_user.id

    # Generate invitation token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    ttl_hours = settings.invitation_ttl_hours
    expires_at = now + timedelta(hours=ttl_hours)

    inv_token = InvitationToken(
        id=str(uuid.uuid4()),
        user_id=new_user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(inv_token)

    await db.commit()
    await db.refresh(new_user)

    record_auth_invitation(OutcomeCode.INVITATION_ISSUED)
    _log.info(
        "Invitation issued by admin",
        extra={
            "event_name": AuthEvent.USER_INVITED,
            "outcome_code": OutcomeCode.INVITATION_ISSUED,
            "invitee_id": new_user.id,
            "admin_user_id": current_user.id,
        },
    )

    # Side-effect: send the invitation email if the email backend is configured.
    # This must run AFTER commit so the user row is durable before the email goes out.
    # Failures are caught inside send_invitation_email — they never abort this response.
    from app.services.invitation_email import send_invitation_email  # noqa: PLC0415
    await send_invitation_email(new_user, raw_token, ttl_hours)

    return InviteUserResponse(
        user=AdminUserResponse.from_user(new_user),
        raw_token=raw_token,
        expires_at=expires_at.isoformat(),
    )


# ── Update user ───────────────────────────────────────────────────────────────


@router.put("/{user_id}", response_model=AdminUserResponse, summary="Update user (admin)")
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    current_user: _UsersManageUser,
    db: AsyncSession = Depends(get_db),
) -> AdminUserResponse:
    """Update a user's profile assignment and/or display name.

    If ``profile_id`` changes away from the admin profile for the last admin,
    the request is rejected with 409 (anti-bricking guard).
    """
    user = await _get_user_or_404(user_id, db)

    if body.profile_id is not None and body.profile_id != user.profile_id:
        # Guard: if this user currently holds the admin profile, demoting them
        # must not leave zero admins.
        await _guard_last_admin(user, db, "demote")

        # Validate new profile exists
        new_profile: Profile | None = await db.get(Profile, body.profile_id)
        if new_profile is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "invalid_profile_id", "message": "Profile not found."},
            )

        old_profile_name = user.profile.name if user.profile else None
        user.profile_id = body.profile_id
        # Sync is_admin flag with admin profile membership
        user.is_admin = (new_profile.name == "admin")

        _log.info(
            "User profile changed by admin",
            extra={
                "event_name": AuthEvent.USER_PROFILE_CHANGED,
                "outcome_code": OutcomeCode.OK,
                "user_id": user.id,
                "admin_user_id": current_user.id,
                "old_profile": old_profile_name,
                "new_profile": new_profile.name,
            },
        )

    if body.display_name is not None:
        user.display_name = body.display_name

    await db.commit()
    await db.refresh(user)
    return AdminUserResponse.from_user(user)


# ── Unlock account ────────────────────────────────────────────────────────────
# This endpoint predates the permission model and uses the legacy require_admin
# dependency.  It is kept here as-is for backward compatibility (SFBL-191).


@router.post(
    "/{user_id}/unlock",
    response_model=UserResponse,
    summary="Unlock an account (admin only)",
)
async def admin_unlock_user(
    user_id: str,
    current_user: _UsersManageUser,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Clear tier-1 and/or tier-2 lockout for the target user.

    - Clears ``locked_until`` (tier-1 auto-lock).
    - Resets ``failed_login_count`` to 0.
    - If ``status == 'locked'``, transitions it back to ``'active'``.
    - Refuses to act on the caller's own user id (400) to prevent
      self-unlock ambiguity.
    - Persists an audit ``login_attempt`` row with ``outcome=admin_unlock``.
    - Emits ``auth.account.unlocked`` with ``outcome_code=admin_manual``.
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot unlock your own account",
        )

    target = await _get_user_or_404(user_id, db)
    now = datetime.now(timezone.utc)

    # Apply unlock
    target.locked_until = None
    target.failed_login_count = 0
    if target.status == "locked":
        target.status = "active"

    # Audit row
    audit_row = LoginAttempt(
        id=str(uuid.uuid4()),
        user_id=target.id,
        username=target.email,
        ip="admin",
        user_agent=None,
        outcome=OutcomeCode.ADMIN_UNLOCK,
        attempted_at=now,
    )
    db.add(audit_row)

    await db.commit()
    await db.refresh(target)

    _log.info(
        "Account unlocked by admin",
        extra={
            "event_name": AuthEvent.ACCOUNT_UNLOCKED,
            "outcome_code": OutcomeCode.ADMIN_MANUAL,
            "user_id": target.id,
            "admin_user_id": current_user.id,
        },
    )
    record_account_unlocked(OutcomeCode.ADMIN_MANUAL)

    return UserResponse.model_validate(target)


# ── Deactivate ────────────────────────────────────────────────────────────────


@router.post("/{user_id}/deactivate", response_model=AdminUserResponse, summary="Deactivate user (admin)")
async def deactivate_user(
    user_id: str,
    current_user: _UsersManageUser,
    db: AsyncSession = Depends(get_db),
) -> AdminUserResponse:
    """Transition a user from ``active`` → ``deactivated``.

    Returns 409 if the source status is not ``active`` or if this would
    leave zero active administrators.
    """
    user = await _get_user_or_404(user_id, db)

    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "invalid_status_transition",
                "current_status": user.status,
                "message": f"Cannot deactivate a user with status '{user.status}'. Expected 'active'.",
            },
        )

    await _guard_last_admin(user, db, "deactivate")

    user.status = "deactivated"
    await db.commit()
    await db.refresh(user)

    _log.info(
        "User deactivated by admin",
        extra={
            "event_name": AuthEvent.USER_DEACTIVATED,
            "outcome_code": OutcomeCode.OK,
            "user_id": user.id,
            "admin_user_id": current_user.id,
        },
    )
    return AdminUserResponse.from_user(user)


# ── Reactivate ────────────────────────────────────────────────────────────────


@router.post("/{user_id}/reactivate", response_model=AdminUserResponse, summary="Reactivate user (admin)")
async def reactivate_user(
    user_id: str,
    current_user: _UsersManageUser,
    db: AsyncSession = Depends(get_db),
) -> AdminUserResponse:
    """Transition a user from ``deactivated`` → ``active``.

    Returns 409 if the source status is not ``deactivated``.
    """
    user = await _get_user_or_404(user_id, db)

    if user.status != "deactivated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "invalid_status_transition",
                "current_status": user.status,
                "message": f"Cannot reactivate a user with status '{user.status}'. Expected 'deactivated'.",
            },
        )

    user.status = "active"
    await db.commit()
    await db.refresh(user)

    _log.info(
        "User reactivated by admin",
        extra={
            "event_name": AuthEvent.USER_REACTIVATED,
            "outcome_code": OutcomeCode.OK,
            "user_id": user.id,
            "admin_user_id": current_user.id,
        },
    )
    return AdminUserResponse.from_user(user)


# ── Reset password (admin) ────────────────────────────────────────────────────


@router.post(
    "/{user_id}/reset-password",
    response_model=AdminResetPasswordResponse,
    summary="Admin-issued temp password reset",
)
async def admin_reset_password(
    user_id: str,
    current_user: _UsersManageUser,
    db: AsyncSession = Depends(get_db),
) -> AdminResetPasswordResponse:
    """Generate a 16-character temporary password for the target user.

    - Sets ``must_reset_password=True`` so the user is forced to change it
      on next login.
    - Updates ``password_changed_at`` (JWT watermark) to invalidate any
      existing sessions held by the target user.
    - The raw temp password is returned once in this response; it is not
      stored.  SFBL-202 will wire email delivery of the temp password.
    """
    user = await _get_user_or_404(user_id, db)

    if user.status == "deleted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "user_deleted", "message": "Cannot reset password for a deleted user."},
        )

    # Generate a URL-safe temp password (~128 bits of entropy)
    temp_password = secrets.token_urlsafe(12)[:16]
    user.hashed_password = hash_password(temp_password)
    user.must_reset_password = True
    user.password_changed_at = datetime.now(timezone.utc)

    await db.commit()

    _log.info(
        "Temp password issued by admin",
        extra={
            "event_name": AuthEvent.TEMP_PASSWORD_ISSUED,
            "outcome_code": OutcomeCode.OK,
            "user_id": user.id,
            "admin_user_id": current_user.id,
        },
    )

    return AdminResetPasswordResponse(temp_password=temp_password, must_reset_password=True)


# ── Resend invitation ─────────────────────────────────────────────────────────


@router.post(
    "/{user_id}/resend-invite",
    response_model=ResendInviteResponse,
    summary="Resend invitation token (admin)",
)
async def resend_invite(
    user_id: str,
    current_user: _UsersManageUser,
    db: AsyncSession = Depends(get_db),
) -> ResendInviteResponse:
    """Issue a fresh InvitationToken for a user whose status is ``invited``.

    The old tokens are left in place (they will naturally expire).  The new
    raw token is returned once in this response for SFBL-202 email dispatch.
    """
    user = await _get_user_or_404(user_id, db)

    if user.status != "invited":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "invalid_status_transition",
                "current_status": user.status,
                "message": f"Cannot resend invitation to a user with status '{user.status}'. Expected 'invited'.",
            },
        )

    now = datetime.now(timezone.utc)
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    ttl_hours = settings.invitation_ttl_hours
    expires_at = now + timedelta(hours=ttl_hours)

    inv_token = InvitationToken(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(inv_token)
    await db.commit()

    record_auth_invitation(OutcomeCode.OK)
    _log.info(
        "Invitation resent by admin",
        extra={
            "event_name": AuthEvent.INVITE_RESENT,
            "outcome_code": OutcomeCode.OK,
            "user_id": user.id,
            "admin_user_id": current_user.id,
        },
    )

    # Side-effect: send the invitation email if the email backend is configured.
    from app.services.invitation_email import send_invitation_email  # noqa: PLC0415
    await send_invitation_email(user, raw_token, ttl_hours)

    return ResendInviteResponse(raw_token=raw_token, expires_at=expires_at.isoformat())


# ── Soft delete ───────────────────────────────────────────────────────────────


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Soft-delete user (admin)")
async def delete_user(
    user_id: str,
    current_user: _UsersManageUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Set ``status='deleted'`` on the target user (tombstone / soft delete).

    Restrictions:
    - The bootstrap admin (``settings.admin_email``) cannot be deleted.
    - The last active admin cannot be deleted (anti-bricking guard).
    - Callers cannot delete themselves (use a separate admin account first).
    """
    user = await _get_user_or_404(user_id, db)

    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "self_delete", "message": "Cannot delete your own account."},
        )

    if user.email == settings.admin_email:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "bootstrap_admin_protected",
                "message": "The bootstrap administrator account cannot be deleted.",
            },
        )

    if user.status == "deleted":
        # Idempotent — already deleted; return 204 without error.
        return

    await _guard_last_admin(user, db, "delete")

    user.status = "deleted"
    await db.commit()

    _log.info(
        "User deleted (soft) by admin",
        extra={
            "event_name": AuthEvent.USER_DELETED,
            "outcome_code": OutcomeCode.OK,
            "user_id": user.id,
            "admin_user_id": current_user.id,
        },
    )
