"""Break-glass CLI subcommand implementations (SFBL-193).

Each public function corresponds to one CLI subcommand.  All functions are
synchronous wrappers around async DB operations, invoked via ``asyncio.run``.

Security note: anyone with shell access to the backend container can call
``admin-recover`` without credentials.  This is intentional — break-glass
access is a last resort when no other admin login is available.  Ensure the
container is not reachable from untrusted networks.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import sys
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete as sa_delete, select

from app.database import AsyncSessionLocal
from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.models.user_backup_code import UserBackupCode
from app.models.user_totp import UserTotp
from app.observability.events import AuthEvent, MfaEvent, OutcomeCode
from app.services.auth import hash_password

_log = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

_CLI_IP = "<cli>"
_CLI_UA = "<cli>"


async def _find_user_by_email(email: str) -> Optional[User]:
    """Return a User matching *email* (email is the unique login identifier post SFBL-198)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()


async def _save_login_attempt(
    user_id: Optional[str],
    username: str,
    outcome: str,
) -> None:
    """Persist a LoginAttempt row for audit purposes."""
    async with AsyncSessionLocal() as session:
        attempt = LoginAttempt(
            user_id=user_id,
            username=username,
            ip=_CLI_IP,
            user_agent=_CLI_UA,
            outcome=outcome,
            attempted_at=datetime.now(timezone.utc),
        )
        session.add(attempt)
        await session.commit()


# ── Subcommand: admin-recover ─────────────────────────────────────────────────


async def _do_admin_recover(email: str, *, reset_2fa: bool = True) -> None:
    """Core async implementation of admin-recover.

    When *reset_2fa* is True (the default), any TOTP factor + backup codes
    belonging to the recovered admin are cleared so the admin can log in
    with just the new temporary password.  This is the break-glass path
    for a lost authenticator; pass ``--keep-2fa`` to preserve it.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.email == email
            )
        )
        user: Optional[User] = result.scalar_one_or_none()

        if user is None:
            print(f"ERROR: No user found for '{email}'.", file=sys.stderr)
            sys.exit(2)

        if not user.is_admin:
            print(
                f"ERROR: User '{email}' is not an admin (is_admin=False). "
                "admin-recover is restricted to admin accounts.",
                file=sys.stderr,
            )
            sys.exit(3)

        if user.status == "deleted":
            print(
                f"ERROR: User '{email}' has been deleted and cannot be recovered. "
                "Create a new admin account instead.",
                file=sys.stderr,
            )
            sys.exit(4)

        # Generate a secure temporary password
        temp_password = secrets.token_urlsafe(16)

        # Update user state
        user.hashed_password = hash_password(temp_password)
        user.must_reset_password = True
        user.locked_until = None
        user.failed_login_count = 0
        # Transition to active from any recoverable status
        user.status = "active"

        had_factor = False
        backup_codes_cleared = 0
        if reset_2fa:
            totp_row = (
                await session.execute(
                    select(UserTotp).where(UserTotp.user_id == user.id)
                )
            ).scalar_one_or_none()
            had_factor = totp_row is not None
            backup_count_result = await session.execute(
                select(UserBackupCode).where(UserBackupCode.user_id == user.id)
            )
            backup_codes_cleared = len(backup_count_result.scalars().all())
            if had_factor:
                await session.execute(
                    sa_delete(UserTotp).where(UserTotp.user_id == user.id)
                )
            if backup_codes_cleared:
                await session.execute(
                    sa_delete(UserBackupCode).where(UserBackupCode.user_id == user.id)
                )

        session.add(user)
        await session.commit()

    # Emit audit log (WARNING for high visibility)
    _log.warning(
        "Break-glass admin recovery performed via CLI",
        extra={
            "event_name": AuthEvent.ADMIN_RECOVERED,
            "outcome_code": OutcomeCode.CLI_RECOVERY,
            "user_id": user.id,
            "username": email,
        },
    )
    if reset_2fa and (had_factor or backup_codes_cleared):
        _log.warning(
            "2FA factor cleared via break-glass CLI",
            extra={
                "event_name": MfaEvent.ADMIN_RESET,
                "outcome_code": OutcomeCode.CLI_RECOVERY,
                "user_id": user.id,
                "username": email,
                "had_factor": had_factor,
                "backup_codes_cleared": backup_codes_cleared,
            },
        )

    # Persist LoginAttempt row
    await _save_login_attempt(
        user_id=user.id,
        username=email,
        outcome=OutcomeCode.CLI_RECOVERY,
    )

    # Print the temp password once
    print()
    print("=" * 60)
    print("  BREAK-GLASS ADMIN RECOVERY")
    print("=" * 60)
    print(f"  User:             {email}")
    print(f"  Temporary password: {temp_password}")
    print()
    print("  The user MUST change this password on first login.")
    print("  Store it securely — it will not be shown again.")
    print("=" * 60)
    print()


def cmd_admin_recover(email: str, *, reset_2fa: bool = True) -> None:
    """Reset an admin user password and unblock the account.

    By default also clears the admin's TOTP factor + backup codes
    (``reset_2fa=True``) — the common case when an authenticator is lost.
    Pass ``reset_2fa=False`` (CLI ``--keep-2fa``) to preserve the factor.

    Exits non-zero if:
    - no user is found for the given email (exit 2)
    - the user is not an admin (exit 3)
    - the user has status='deleted' (exit 4)
    """
    asyncio.run(_do_admin_recover(email, reset_2fa=reset_2fa))


# ── Subcommand: unlock ────────────────────────────────────────────────────────


async def _do_unlock(email: str) -> None:
    """Core async implementation of unlock."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.email == email
            )
        )
        user: Optional[User] = result.scalar_one_or_none()

        if user is None:
            print(f"ERROR: No user found for '{email}'.", file=sys.stderr)
            sys.exit(2)

        prev_status = user.status
        user.locked_until = None
        user.failed_login_count = 0
        if user.status == "locked":
            user.status = "active"

        session.add(user)
        await session.commit()

    new_status = user.status
    print(f"User '{email}' unlocked successfully.")
    if prev_status != new_status:
        print(f"  Status changed: {prev_status} → {new_status}")
    print("  locked_until cleared, failed_login_count reset to 0.")


def cmd_unlock(email: str) -> None:
    """Clear login lockout for any user (no password change).

    Exits non-zero if no user is found for the given email (exit 2).
    """
    asyncio.run(_do_unlock(email))


# ── Subcommand: list-admins ───────────────────────────────────────────────────


async def _do_list_admins() -> None:
    """Core async implementation of list-admins."""
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.is_admin.is_(True)).order_by(User.created_at)
        )
        admins = result.scalars().all()

    if not admins:
        print("No admin users found.")
        return

    # Column widths
    col_email = max(len("Email"), *(len(u.email or "<no-email>") for u in admins))
    col_status = max(len("Status"), *(len(u.status) for u in admins))
    col_last = len("Last Login")
    col_locked = len("Currently Locked")

    header = (
        f"{'Email':<{col_email}}  "
        f"{'Status':<{col_status}}  "
        f"{'Last Login':<{col_last}}  "
        f"{'Currently Locked':<{col_locked}}"
    )
    sep = "-" * len(header)

    print()
    print(header)
    print(sep)

    for u in admins:
        email_display = u.email or "<no-email>"

        last_login = "never"
        # last_login_at is not yet on the User model (SFBL-194 scope), fall back gracefully
        if hasattr(u, "last_login_at") and u.last_login_at is not None:
            last_login = u.last_login_at.strftime("%Y-%m-%d %H:%M UTC")

        # Determine if currently locked
        is_locked = u.status == "locked"
        if not is_locked and u.locked_until is not None:
            lu = u.locked_until
            if lu.tzinfo is None:
                lu = lu.replace(tzinfo=timezone.utc)
            if lu > now:
                is_locked = True

        locked_display = "YES" if is_locked else "no"

        print(
            f"{email_display:<{col_email}}  "
            f"{u.status:<{col_status}}  "
            f"{last_login:<{col_last}}  "
            f"{locked_display:<{col_locked}}"
        )

    print()


def cmd_list_admins() -> None:
    """Print a formatted table of all admin users."""
    asyncio.run(_do_list_admins())
