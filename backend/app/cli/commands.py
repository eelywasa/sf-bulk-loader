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

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.observability.events import AuthEvent, OutcomeCode
from app.services.auth import hash_password

_log = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

_CLI_IP = "<cli>"
_CLI_UA = "<cli>"


async def _find_user_by_email(email: str) -> Optional[User]:
    """Return a User matching *email* (searches username then email column)."""
    async with AsyncSessionLocal() as session:
        # username is the primary login identifier; email may also be set
        result = await session.execute(
            select(User).where(
                (User.username == email) | (User.email == email)
            )
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


async def _do_admin_recover(email: str) -> None:
    """Core async implementation of admin-recover."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(
                (User.username == email) | (User.email == email)
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


def cmd_admin_recover(email: str) -> None:
    """Reset an admin user password and unblock the account.

    Exits non-zero if:
    - no user is found for the given email (exit 2)
    - the user is not an admin (exit 3)
    - the user has status='deleted' (exit 4)
    """
    asyncio.run(_do_admin_recover(email))


# ── Subcommand: unlock ────────────────────────────────────────────────────────


async def _do_unlock(email: str) -> None:
    """Core async implementation of unlock."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(
                (User.username == email) | (User.email == email)
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
    col_email = max(len("Email"), *(len(u.username or u.email or "<no-email>") for u in admins))
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
        email_display = u.username or u.email or "<no-email>"

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
