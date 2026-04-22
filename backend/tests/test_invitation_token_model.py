"""Unit tests for the InvitationToken model and User lifecycle columns (SFBL-199).

Coverage:
- InvitationToken: insert, query, token_hash unique constraint, NOT NULL
  constraints on token_hash and expires_at.
- InvitationToken: derived status logic (pending / used / expired).
- InvitationToken: atomic single-use redeem semantics via UPDATE WHERE.
- Concurrency: two simultaneous redeem attempts — only one wins.
- User lifecycle columns: invited_by, invited_at, last_login_at.
- Config: INVITATION_TTL_HOURS default.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.invitation_token import InvitationToken
from app.models.user import User

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_user(**kwargs) -> User:
    defaults = dict(
        id=str(uuid.uuid4()),
        username=None,
        hashed_password="x",
        status="active",
    )
    defaults.update(kwargs)
    return User(**defaults)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _future_dt() -> datetime:
    """Return a timezone-aware datetime 24 hours in the future."""
    from datetime import timedelta
    return datetime.now(timezone.utc) + timedelta(hours=24)


def _past_dt() -> datetime:
    """Return a timezone-aware datetime 24 hours in the past."""
    from datetime import timedelta
    return datetime.now(timezone.utc) - timedelta(hours=24)


# conftest provides _TestSession — we access it via the session fixture below.

# Import the session factory used in conftest so we can open sessions directly.
from tests.conftest import _TestSession  # type: ignore[attr-defined]


async def _get_session() -> AsyncSession:
    """Open a fresh session (caller must close)."""
    return _TestSession()


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def session():
    """Yield a live AsyncSession and roll it back after the test."""
    async with _TestSession() as s:
        yield s


@pytest_asyncio.fixture
async def user(session: AsyncSession) -> User:
    """Seed a single active user for FK use."""
    u = _make_user()
    session.add(u)
    await session.flush()
    return u


# ── config ────────────────────────────────────────────────────────────────────

def test_invitation_ttl_hours_default():
    """INVITATION_TTL_HOURS defaults to 24."""
    assert settings.invitation_ttl_hours == 24


# ── basic insert / query ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_insert_and_query_invitation_token(session: AsyncSession, user: User):
    raw = secrets.token_hex(32)
    token_hash = _hash_token(raw)
    expires = _future_dt()

    token = InvitationToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires,
    )
    session.add(token)
    await session.commit()

    result = await session.execute(
        sa.select(InvitationToken).where(InvitationToken.token_hash == token_hash)
    )
    fetched = result.scalar_one()
    assert fetched.id is not None
    assert fetched.user_id == user.id
    assert fetched.token_hash == token_hash
    assert fetched.used_at is None
    assert fetched.created_at is not None


@pytest.mark.asyncio
async def test_token_hash_unique_constraint(session: AsyncSession, user: User):
    """Inserting two rows with the same token_hash must raise an integrity error."""
    token_hash = _hash_token(secrets.token_hex(32))
    expires = _future_dt()

    session.add(InvitationToken(user_id=user.id, token_hash=token_hash, expires_at=expires))
    await session.commit()

    session.add(InvitationToken(user_id=user.id, token_hash=token_hash, expires_at=expires))
    with pytest.raises(Exception):  # IntegrityError
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_token_hash_not_null(session: AsyncSession, user: User):
    """token_hash NOT NULL — inserting NULL must raise an error."""
    token = InvitationToken(
        user_id=user.id,
        token_hash=None,  # type: ignore[arg-type]
        expires_at=_future_dt(),
    )
    session.add(token)
    with pytest.raises(Exception):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_expires_at_not_null(session: AsyncSession, user: User):
    """expires_at NOT NULL — inserting NULL must raise an error."""
    token = InvitationToken(
        user_id=user.id,
        token_hash=_hash_token(secrets.token_hex(32)),
        expires_at=None,  # type: ignore[arg-type]
    )
    session.add(token)
    with pytest.raises(Exception):
        await session.commit()
    await session.rollback()


# ── derived status logic ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_token(session: AsyncSession, user: User):
    """A token with used_at=NULL and expires_at in the future is 'pending'."""
    token = InvitationToken(
        user_id=user.id,
        token_hash=_hash_token(secrets.token_hex(32)),
        expires_at=_future_dt(),
    )
    session.add(token)
    await session.commit()

    result = await session.execute(
        sa.select(InvitationToken).where(
            InvitationToken.used_at.is_(None),
            InvitationToken.expires_at > sa.func.now(),
            InvitationToken.id == token.id,
        )
    )
    assert result.scalar_one_or_none() is not None, "Token should be pending"


@pytest.mark.asyncio
async def test_used_token_not_pending(session: AsyncSession, user: User):
    """A token with used_at set is no longer pending."""
    token = InvitationToken(
        user_id=user.id,
        token_hash=_hash_token(secrets.token_hex(32)),
        expires_at=_future_dt(),
        used_at=datetime.now(timezone.utc),
    )
    session.add(token)
    await session.commit()

    result = await session.execute(
        sa.select(InvitationToken).where(
            InvitationToken.used_at.is_(None),
            InvitationToken.expires_at > sa.func.now(),
            InvitationToken.id == token.id,
        )
    )
    assert result.scalar_one_or_none() is None, "Used token should not appear as pending"


@pytest.mark.asyncio
async def test_expired_token_not_pending(session: AsyncSession, user: User):
    """A token with expires_at in the past is not pending."""
    token = InvitationToken(
        user_id=user.id,
        token_hash=_hash_token(secrets.token_hex(32)),
        expires_at=_past_dt(),
    )
    session.add(token)
    await session.commit()

    result = await session.execute(
        sa.select(InvitationToken).where(
            InvitationToken.used_at.is_(None),
            InvitationToken.expires_at > sa.func.now(),
            InvitationToken.id == token.id,
        )
    )
    assert result.scalar_one_or_none() is None, "Expired token should not appear as pending"


# ── atomic single-use redeem ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_atomic_redeem_success(session: AsyncSession, user: User):
    """First redeem attempt succeeds (UPDATE affects 1 row)."""
    raw = secrets.token_hex(32)
    token_hash = _hash_token(raw)
    token = InvitationToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=_future_dt(),
    )
    session.add(token)
    await session.commit()

    result = await session.execute(
        sa.update(InvitationToken)
        .where(
            InvitationToken.token_hash == token_hash,
            InvitationToken.used_at.is_(None),
            InvitationToken.expires_at > sa.func.now(),
        )
        .values(used_at=datetime.now(timezone.utc))
        .returning(InvitationToken.id)
    )
    await session.commit()
    row = result.first()
    assert row is not None, "Redeem should succeed and return a row"


@pytest.mark.asyncio
async def test_atomic_redeem_already_used(session: AsyncSession, user: User):
    """Second redeem attempt fails because used_at is now set (UPDATE 0 rows)."""
    raw = secrets.token_hex(32)
    token_hash = _hash_token(raw)
    token = InvitationToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=_future_dt(),
        used_at=datetime.now(timezone.utc),  # pre-used
    )
    session.add(token)
    await session.commit()

    result = await session.execute(
        sa.update(InvitationToken)
        .where(
            InvitationToken.token_hash == token_hash,
            InvitationToken.used_at.is_(None),
            InvitationToken.expires_at > sa.func.now(),
        )
        .values(used_at=datetime.now(timezone.utc))
        .returning(InvitationToken.id)
    )
    await session.commit()
    row = result.first()
    assert row is None, "Second redeem should fail — token already used"


@pytest.mark.asyncio
async def test_atomic_redeem_expired(session: AsyncSession, user: User):
    """Redeem of an expired token fails (UPDATE 0 rows)."""
    raw = secrets.token_hex(32)
    token_hash = _hash_token(raw)
    token = InvitationToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=_past_dt(),
    )
    session.add(token)
    await session.commit()

    result = await session.execute(
        sa.update(InvitationToken)
        .where(
            InvitationToken.token_hash == token_hash,
            InvitationToken.used_at.is_(None),
            InvitationToken.expires_at > sa.func.now(),
        )
        .values(used_at=datetime.now(timezone.utc))
        .returning(InvitationToken.id)
    )
    await session.commit()
    row = result.first()
    assert row is None, "Redeem of expired token should fail"


# ── concurrency: two simultaneous redeems, only one wins ─────────────────────

@pytest.mark.asyncio
async def test_concurrent_redeem_only_one_wins(session: AsyncSession, user: User):
    """Two simultaneous redeem attempts on the same token — exactly one succeeds.

    Each coroutine opens its own session (simulating two concurrent requests).
    The database's write serialisation ensures only one UPDATE matches the
    ``used_at IS NULL`` guard.
    """
    # Seed the token using the shared fixture session so the user FK resolves.
    raw = secrets.token_hex(32)
    token_hash = _hash_token(raw)
    session.add(
        InvitationToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=_future_dt(),
        )
    )
    await session.commit()

    async def _try_redeem() -> bool:
        async with _TestSession() as s:
            result = await s.execute(
                sa.update(InvitationToken)
                .where(
                    InvitationToken.token_hash == token_hash,
                    InvitationToken.used_at.is_(None),
                    InvitationToken.expires_at > sa.func.now(),
                )
                .values(used_at=datetime.now(timezone.utc))
                .returning(InvitationToken.id)
            )
            await s.commit()
            return result.first() is not None

    results = await asyncio.gather(_try_redeem(), _try_redeem())
    winners = sum(results)
    assert winners == 1, (
        f"Exactly one concurrent redeem should win, got {winners} winners"
    )


# ── User lifecycle columns ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_invited_by_column(session: AsyncSession):
    """invited_by FK is nullable and can reference another user."""
    admin = _make_user()
    session.add(admin)
    await session.flush()

    invited = _make_user(
        invited_by=admin.id,
        invited_at=datetime.now(timezone.utc),
        status="invited",
    )
    session.add(invited)
    await session.commit()

    result = await session.execute(
        sa.select(User).where(User.id == invited.id)
    )
    fetched = result.scalar_one()
    assert fetched.invited_by == admin.id
    assert fetched.invited_at is not None


@pytest.mark.asyncio
async def test_user_last_login_at_column(session: AsyncSession):
    """last_login_at is nullable; can be set to a timestamp."""
    u = _make_user()
    session.add(u)
    await session.commit()

    now = datetime.now(timezone.utc)
    await session.execute(
        sa.update(User)
        .where(User.id == u.id)
        .values(last_login_at=now)
    )
    await session.commit()

    result = await session.execute(sa.select(User).where(User.id == u.id))
    fetched = result.scalar_one()
    assert fetched.last_login_at is not None


@pytest.mark.asyncio
async def test_user_lifecycle_columns_default_null(session: AsyncSession):
    """Bootstrap user has all lifecycle columns NULL by default."""
    u = _make_user()
    session.add(u)
    await session.commit()

    result = await session.execute(sa.select(User).where(User.id == u.id))
    fetched = result.scalar_one()
    assert fetched.invited_by is None
    assert fetched.invited_at is None
    assert fetched.last_login_at is None
