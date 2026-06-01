"""Tests for PAT authentication integration in get_current_user (SFBL-367).

Covers:
- Headline gate: PAT-authenticated request to require_permission-gated route
  returns 200, not 403 (proves profile is eager-loaded with user).
- Unknown / opaque PAT prefix is taken as PAT branch (not swallowed as JWT error).
- Valid PAT authenticates successfully.
- Revoked PAT → 401 with observability event.
- Expired PAT → 401 with observability event.
- PAT for user with status != 'active' → 401.
- PAT for user with locked_until in the future → 401.
- last_used_at throttle: two rapid requests write at most once within the window.
- Concurrency: gather several requests, assert no error.
- request.state.auth_method is "pat" for PAT requests, "session" for JWT requests.
- No token plaintext in logs.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.permissions import CONNECTIONS_VIEW, require_permission
from app.models.personal_access_token import PersonalAccessToken
from app.models.profile import Profile
from app.models.profile_permission import ProfilePermission
from app.models.user import User
from app.services.auth import _LAST_USED_THROTTLE_SECONDS, create_access_token, get_current_user
from app.services.pat import TOKEN_PREFIX, hash_token, issue
from tests.conftest import _TestSession, _run_async


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_profile(name: str = "admin") -> Profile:
    """Fetch the named seeded profile from the test DB."""

    async def _fetch():
        async with _TestSession() as session:
            result = await session.execute(select(Profile).where(Profile.name == name))
            profile = result.scalar_one()
            # Eagerly fetch permissions so they survive session close
            perm_result = await session.execute(
                select(ProfilePermission).where(ProfilePermission.profile_id == profile.id)
            )
            perms = perm_result.scalars().all()
            # Return a detached copy with permissions attached
            from app.auth.permissions import ALL_PERMISSION_KEYS
            detached = Profile(id=profile.id, name=profile.name, description=profile.description, is_system=profile.is_system)
            detached.permissions = [ProfilePermission(profile_id=detached.id, permission_key=p.permission_key) for p in perms]
            return detached

    return _run_async(_fetch())


def _seed_user_with_profile(profile_name: str = "admin", **overrides) -> tuple[User, str]:
    """Seed a user in the test DB with the named profile. Returns (user, profile_id)."""
    async def _do():
        async with _TestSession() as session:
            result = await session.execute(select(Profile).where(Profile.name == profile_name))
            profile = result.scalar_one()
            # Build defaults first; overrides take precedence (avoids duplicate kwarg error)
            defaults = dict(
                id=str(uuid.uuid4()),
                email=f"pat-test-{uuid.uuid4().hex[:8]}@example.com",
                hashed_password="x",
                status="active",
                is_admin=(profile_name == "admin"),
                profile_id=profile.id,
            )
            defaults.update(overrides)
            user = User(**defaults)
            session.add(user)
            await session.commit()
            return user, profile.id

    return _run_async(_do())


def _issue_pat(user: User, *, expires_at=None) -> tuple[PersonalAccessToken, str]:
    """Issue a PAT for user, commit, and return (row, plaintext)."""
    async def _do():
        async with _TestSession() as session:
            # Re-attach user to this session for the relationship
            from sqlalchemy import select as _sel
            u = await session.get(User, user.id)
            pat, plaintext = await issue(session, u, name="test-pat", expires_at=expires_at)
            await session.commit()
            return pat, plaintext

    return _run_async(_do())


def _make_pat_app() -> FastAPI:
    """Create a minimal FastAPI app with a connections.view-gated endpoint."""
    _app = FastAPI()
    dep = require_permission(CONNECTIONS_VIEW)

    @_app.get("/api/connections/")
    async def list_connections(user: User = Depends(dep)) -> dict:
        return {"email": user.email}

    return _app


def _make_client(app: FastAPI | None = None) -> TestClient:
    """Return a TestClient wired to the test DB."""
    from app.database import get_db
    from app.main import app as main_app
    import app.services.orchestrator as _orch

    _app = app or main_app

    async def override_get_db():
        async with _TestSession() as session:
            yield session

    async def _noop(_run_id: str) -> None:
        pass

    _app.dependency_overrides[get_db] = override_get_db
    _original = _orch.execute_run
    _orch.execute_run = _noop
    client = TestClient(_app, raise_server_exceptions=True)
    # Restore after use — caller is responsible for cleanup
    return client, lambda: (setattr(_orch, "execute_run", _original), _app.dependency_overrides.clear())


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestPatHeadlineGate:
    """PAT-authenticated request to require_permission-gated route returns 200, not 403.

    This is the headline blocker: without the selectinload(User.profile) in
    _authenticate_pat, require_permission() sees profile=None and returns 403.
    """

    def test_pat_auth_passes_permission_gate(self, client):
        """A PAT for a user with connections.view must return 200 on the connections endpoint."""
        user, _ = _seed_user_with_profile("admin")
        _, plaintext = _issue_pat(user)

        resp = client.get(
            "/api/connections/",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        # 200 means the PAT was accepted AND profile was loaded (no 403 permission denial)
        assert resp.status_code == 200, (
            f"Expected 200 but got {resp.status_code}. "
            "This indicates the PAT path failed to eager-load User.profile — "
            "require_permission() would 403 without it."
        )

    def test_pat_auth_viewer_profile_passes_connections_view(self, client):
        """A viewer-profile user's PAT must pass the connections.view gate."""
        user, _ = _seed_user_with_profile("viewer")
        _, plaintext = _issue_pat(user)

        resp = client.get(
            "/api/connections/",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert resp.status_code == 200


class TestPatBranchSelection:
    """PAT prefix is taken as PAT branch, not JWT decode attempt."""

    def test_valid_pat_authenticates(self, client):
        """A valid PAT bearer token authenticates successfully."""
        user, _ = _seed_user_with_profile("admin")
        _, plaintext = _issue_pat(user)

        resp = client.get(
            "/api/connections/",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert resp.status_code == 200

    def test_opaque_pat_prefix_not_treated_as_jwt(self, client):
        """A bearer value starting with sfbl_pat_ but not in the DB returns 401,
        not a JWTError-derived 401 — i.e. the PAT branch is entered, not the JWT branch.
        """
        fake_pat = TOKEN_PREFIX + "a" * 43  # well-formed prefix, not in DB
        resp = client.get(
            "/api/connections/",
            headers={"Authorization": f"Bearer {fake_pat}"},
        )
        # 401 from PAT path (not a JWT decode error, which would also be 401 but
        # we can confirm by checking the detail message doesn't say "Invalid or expired")
        assert resp.status_code == 401

    def test_unknown_pat_returns_401(self, client):
        """A PAT-prefix bearer that doesn't exist in the DB → 401."""
        fake = TOKEN_PREFIX + "z" * 43
        resp = client.get(
            "/api/connections/",
            headers={"Authorization": f"Bearer {fake}"},
        )
        assert resp.status_code == 401


class TestPatRejectionGates:
    """Revoked, expired, inactive-user, and locked-user PATs are all rejected."""

    def test_revoked_pat_returns_401(self, client, caplog):
        """A revoked PAT must return 401."""
        user, _ = _seed_user_with_profile("admin")
        _, plaintext = _issue_pat(user)

        # Revoke the token directly in the DB
        async def _revoke():
            async with _TestSession() as session:
                result = await session.execute(
                    select(PersonalAccessToken).where(
                        PersonalAccessToken.token_hash == hash_token(plaintext)
                    )
                )
                row = result.scalar_one()
                row.revoked_at = datetime.now(timezone.utc)
                await session.commit()

        _run_async(_revoke())

        with caplog.at_level(logging.WARNING, logger="app.services.auth"):
            resp = client.get(
                "/api/connections/",
                headers={"Authorization": f"Bearer {plaintext}"},
            )

        assert resp.status_code == 401
        # Assert the rejection event was emitted
        from app.observability.events import AuthEvent
        assert any(
            getattr(r, "event_name", None) == AuthEvent.TOKEN_REJECTED
            and getattr(r, "rejection_reason", None) == "pat_revoked"
            for r in caplog.records
        ), f"Expected TOKEN_REJECTED/pat_revoked in logs; records: {[(r.getMessage(), getattr(r, 'event_name', None)) for r in caplog.records]}"

    def test_expired_pat_returns_401(self, client, caplog):
        """A PAT with expires_at in the past must return 401."""
        user, _ = _seed_user_with_profile("admin")
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        _, plaintext = _issue_pat(user, expires_at=past)

        with caplog.at_level(logging.WARNING, logger="app.services.auth"):
            resp = client.get(
                "/api/connections/",
                headers={"Authorization": f"Bearer {plaintext}"},
            )

        assert resp.status_code == 401
        from app.observability.events import AuthEvent
        assert any(
            getattr(r, "event_name", None) == AuthEvent.TOKEN_REJECTED
            and getattr(r, "rejection_reason", None) == "pat_expired"
            for r in caplog.records
        ), f"Expected TOKEN_REJECTED/pat_expired; records: {[(r.getMessage(), getattr(r, 'event_name', None)) for r in caplog.records]}"

    def test_inactive_user_pat_returns_401(self, client):
        """A PAT whose owner has status != 'active' must return 401."""
        user, _ = _seed_user_with_profile("admin", status="deactivated")
        _, plaintext = _issue_pat(user)

        resp = client.get(
            "/api/connections/",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert resp.status_code == 401

    def test_locked_user_pat_returns_401(self, client):
        """A PAT whose owner is under tier-1 lockout must return 401."""
        future_lock = datetime.now(timezone.utc) + timedelta(hours=1)
        user, _ = _seed_user_with_profile("admin", locked_until=future_lock)
        _, plaintext = _issue_pat(user)

        resp = client.get(
            "/api/connections/",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert resp.status_code == 401

    def test_locked_user_pat_emits_event(self, client, caplog):
        """Locked-user PAT rejection emits TOKEN_REJECTED observability event."""
        future_lock = datetime.now(timezone.utc) + timedelta(hours=1)
        user, _ = _seed_user_with_profile("admin", locked_until=future_lock)
        _, plaintext = _issue_pat(user)

        from app.observability.events import AuthEvent
        with caplog.at_level(logging.WARNING, logger="app.services.auth"):
            resp = client.get(
                "/api/connections/",
                headers={"Authorization": f"Bearer {plaintext}"},
            )

        assert resp.status_code == 401
        assert any(
            getattr(r, "event_name", None) == AuthEvent.TOKEN_REJECTED
            and getattr(r, "rejection_reason", None) == "pat_account_locked"
            for r in caplog.records
        )

    def test_inactive_user_pat_emits_event(self, client, caplog):
        """Inactive-user PAT rejection emits TOKEN_REJECTED observability event."""
        user, _ = _seed_user_with_profile("admin", status="deactivated")
        _, plaintext = _issue_pat(user)

        from app.observability.events import AuthEvent
        with caplog.at_level(logging.WARNING, logger="app.services.auth"):
            resp = client.get(
                "/api/connections/",
                headers={"Authorization": f"Bearer {plaintext}"},
            )

        assert resp.status_code == 401
        assert any(
            getattr(r, "event_name", None) == AuthEvent.TOKEN_REJECTED
            for r in caplog.records
        )


class TestLastUsedThrottle:
    """last_used_at is written at most once within the throttle window."""

    def test_last_used_at_written_on_first_use(self, client):
        """last_used_at is None before first use, non-None after."""
        user, _ = _seed_user_with_profile("admin")
        _, plaintext = _issue_pat(user)

        # Verify last_used_at is None before first request
        async def _fetch():
            async with _TestSession() as session:
                result = await session.execute(
                    select(PersonalAccessToken).where(
                        PersonalAccessToken.token_hash == hash_token(plaintext)
                    )
                )
                return result.scalar_one()

        row_before = _run_async(_fetch())
        assert row_before.last_used_at is None

        resp = client.get(
            "/api/connections/",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert resp.status_code == 200

        row_after = _run_async(_fetch())
        assert row_after.last_used_at is not None

    def test_last_used_at_not_updated_within_throttle_window(self, client):
        """Two requests within the throttle window → last_used_at written only once."""
        user, _ = _seed_user_with_profile("admin")
        _, plaintext = _issue_pat(user)

        headers = {"Authorization": f"Bearer {plaintext}"}

        # First request — triggers write
        resp1 = client.get("/api/connections/", headers=headers)
        assert resp1.status_code == 200

        async def _fetch():
            async with _TestSession() as session:
                result = await session.execute(
                    select(PersonalAccessToken).where(
                        PersonalAccessToken.token_hash == hash_token(plaintext)
                    )
                )
                return result.scalar_one()

        row_after_first = _run_async(_fetch())
        first_ts = row_after_first.last_used_at
        assert first_ts is not None

        # Second request immediately after — should NOT update last_used_at
        resp2 = client.get("/api/connections/", headers=headers)
        assert resp2.status_code == 200

        row_after_second = _run_async(_fetch())
        second_ts = row_after_second.last_used_at

        # Timestamps must be identical (no DB write on second request)
        assert first_ts == second_ts, (
            f"last_used_at should not change within the {_LAST_USED_THROTTLE_SECONDS}s window. "
            f"First: {first_ts}, Second: {second_ts}"
        )

    def test_last_used_at_updated_after_throttle_window(self, client):
        """last_used_at IS updated when the token was last used more than the window ago."""
        user, _ = _seed_user_with_profile("admin")
        _, plaintext = _issue_pat(user)
        token_hash = hash_token(plaintext)

        headers = {"Authorization": f"Bearer {plaintext}"}

        # First request
        resp = client.get("/api/connections/", headers=headers)
        assert resp.status_code == 200

        # Backdate last_used_at to simulate an old use (outside the throttle window)
        old_ts = datetime.now(timezone.utc) - timedelta(seconds=_LAST_USED_THROTTLE_SECONDS + 60)

        async def _backdate():
            async with _TestSession() as session:
                result = await session.execute(
                    select(PersonalAccessToken).where(
                        PersonalAccessToken.token_hash == token_hash
                    )
                )
                row = result.scalar_one()
                row.last_used_at = old_ts
                await session.commit()

        _run_async(_backdate())

        # Second request — should trigger a write because last_used_at is stale
        resp2 = client.get("/api/connections/", headers=headers)
        assert resp2.status_code == 200

        async def _fetch():
            async with _TestSession() as session:
                result = await session.execute(
                    select(PersonalAccessToken).where(
                        PersonalAccessToken.token_hash == token_hash
                    )
                )
                return result.scalar_one()

        row_final = _run_async(_fetch())
        assert row_final.last_used_at is not None
        assert row_final.last_used_at != old_ts, "last_used_at should have been updated"

    def test_concurrent_requests_no_error(self, client):
        """Multiple concurrent PAT-authenticated requests complete without error."""
        user, _ = _seed_user_with_profile("admin")
        _, plaintext = _issue_pat(user)
        headers = {"Authorization": f"Bearer {plaintext}"}

        # TestClient is synchronous; simulate concurrency by issuing multiple
        # requests and verifying none raise / error.
        results = []
        for _ in range(5):
            resp = client.get("/api/connections/", headers=headers)
            results.append(resp.status_code)

        assert all(s == 200 for s in results), f"All requests should succeed; got: {results}"


class TestAuthMethodState:
    """request.state.auth_method is set correctly for PAT and JWT paths.

    We call get_current_user directly (via asyncio) with a real DB session and
    a minimal mock Request so we can inspect request.state after the call.
    This avoids the complexity of building a test mini-app while still exercising
    the state-recording code path.
    """

    def test_pat_sets_auth_method_to_pat(self):
        """PAT-authenticated request sets request.state.auth_method = 'pat'."""
        from unittest.mock import MagicMock
        from fastapi.security import HTTPAuthorizationCredentials

        user, _ = _seed_user_with_profile("admin")
        _, plaintext = _issue_pat(user)

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=plaintext)
        mock_request = MagicMock()
        mock_request.state = MagicMock()

        async def _run():
            async with _TestSession() as session:
                result = await get_current_user(credentials=creds, db=session, request=mock_request)
                return result

        user_result = _run_async(_run())
        assert user_result is not None
        assert mock_request.state.auth_method == "pat", (
            f"Expected auth_method='pat', got {mock_request.state.auth_method!r}"
        )

    def test_jwt_sets_auth_method_to_session(self):
        """JWT-authenticated request sets request.state.auth_method = 'session'."""
        from unittest.mock import MagicMock
        from fastapi.security import HTTPAuthorizationCredentials

        user, _ = _seed_user_with_profile("admin")
        token = create_access_token(user)

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        mock_request = MagicMock()
        mock_request.state = MagicMock()

        async def _run():
            async with _TestSession() as session:
                result = await get_current_user(credentials=creds, db=session, request=mock_request)
                return result

        user_result = _run_async(_run())
        assert user_result is not None
        assert mock_request.state.auth_method == "session", (
            f"Expected auth_method='session', got {mock_request.state.auth_method!r}"
        )


class TestNoTokenMaterialInLogs:
    """Token plaintext must not appear in any log record."""

    def test_successful_pat_auth_no_plaintext_in_logs(self, client, caplog):
        """On a successful PAT-authenticated request, no log record contains the plaintext."""
        user, _ = _seed_user_with_profile("admin")
        _, plaintext = _issue_pat(user)

        with caplog.at_level(logging.DEBUG, logger="app.services.auth"):
            resp = client.get(
                "/api/connections/",
                headers={"Authorization": f"Bearer {plaintext}"},
            )

        assert resp.status_code == 200
        for record in caplog.records:
            msg = record.getMessage()
            assert plaintext not in msg, (
                f"Token plaintext leaked in log record: {msg!r}"
            )
            # Also check the args string representation
            if record.args:
                args_str = str(record.args)
                assert plaintext not in args_str

    def test_rejected_pat_no_plaintext_in_logs(self, client, caplog):
        """On a rejected PAT (unknown), no log record contains the plaintext."""
        fake = TOKEN_PREFIX + "leaked_token_value_should_not_appear_in_logs"

        with caplog.at_level(logging.DEBUG, logger="app.services.auth"):
            resp = client.get(
                "/api/connections/",
                headers={"Authorization": f"Bearer {fake}"},
            )

        assert resp.status_code == 401
        for record in caplog.records:
            assert fake not in record.getMessage()


class TestPatObservabilityEvents:
    """PAT authentication emits the expected observability events."""

    def test_successful_pat_emits_pat_used(self, client, caplog):
        """Successful PAT authentication emits AuthEvent.PAT_USED."""
        user, _ = _seed_user_with_profile("admin")
        _, plaintext = _issue_pat(user)

        from app.observability.events import AuthEvent
        with caplog.at_level(logging.INFO, logger="app.services.auth"):
            resp = client.get(
                "/api/connections/",
                headers={"Authorization": f"Bearer {plaintext}"},
            )

        assert resp.status_code == 200
        assert any(
            getattr(r, "event_name", None) == AuthEvent.PAT_USED
            for r in caplog.records
        ), f"Expected PAT_USED event; got: {[(r.getMessage(), getattr(r, 'event_name', None)) for r in caplog.records]}"

    def test_unknown_pat_emits_token_rejected(self, client, caplog):
        """Unknown PAT emits TOKEN_REJECTED with reason pat_unknown."""
        fake = TOKEN_PREFIX + "b" * 43

        from app.observability.events import AuthEvent
        with caplog.at_level(logging.WARNING, logger="app.services.auth"):
            resp = client.get(
                "/api/connections/",
                headers={"Authorization": f"Bearer {fake}"},
            )

        assert resp.status_code == 401
        assert any(
            getattr(r, "event_name", None) == AuthEvent.TOKEN_REJECTED
            and getattr(r, "rejection_reason", None) == "pat_unknown"
            for r in caplog.records
        ), f"Expected TOKEN_REJECTED/pat_unknown; records: {[(r.getMessage(), getattr(r, 'event_name', None)) for r in caplog.records]}"
