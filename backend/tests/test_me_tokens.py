"""Tests for the PAT management API — /api/me/tokens (SFBL-368).

Covers:
- POST /api/me/tokens: creates a token, returns plaintext ONCE in response.
- GET /api/me/tokens: returns metadata only (no plaintext/hash).
- DELETE /api/me/tokens/{id}: revokes a token.
- Permission enforcement: tokens.manage required for all three routes.
- Ownership: user can only see/revoke their own tokens.
- require_session_auth: PAT-authenticated POST/DELETE → 403 "session_required".
- Session-authenticated create → 201.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.profile import Profile
from app.models.profile_permission import ProfilePermission
from app.models.user import User
from app.services.auth import get_current_user
from tests.conftest import _TestSession, _run_async


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_profile(keys: list[str]) -> Profile:
    """Build an in-memory Profile with the given permission keys."""
    profile = Profile(id=str(uuid.uuid4()), name=f"profile-{uuid.uuid4().hex[:6]}")
    profile.permissions = [ProfilePermission(permission_key=k) for k in keys]
    return profile


def _seed_user_with_profile(keys: list[str]) -> User:
    """Create and persist a User in the test DB with an in-memory profile."""
    user = User(
        id=str(uuid.uuid4()),
        email=f"pat-test-{uuid.uuid4().hex[:6]}@example.com",
        hashed_password="x",
        status="active",
    )
    user.profile = _make_profile(keys)

    async def _do() -> None:
        async with _TestSession() as s:
            s.add(user)
            await s.commit()

    _run_async(_do())
    return user


def _override_user(user: User):
    async def _dep():
        return user
    return _dep


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def admin_user():
    """Admin user with tokens.manage persisted in DB."""
    from app.auth.permissions import (
        TOKENS_MANAGE, NOTIFICATIONS_MANAGE,
        CONNECTIONS_VIEW, CONNECTIONS_VIEW_CREDENTIALS, CONNECTIONS_MANAGE,
        PLANS_VIEW, PLANS_MANAGE, RUNS_VIEW, RUNS_EXECUTE, RUNS_ABORT,
        FILES_VIEW, FILES_VIEW_CONTENTS, USERS_MANAGE, USERS_RESET_2FA,
        SYSTEM_SETTINGS,
    )
    return _seed_user_with_profile([
        CONNECTIONS_VIEW, CONNECTIONS_VIEW_CREDENTIALS, CONNECTIONS_MANAGE,
        PLANS_VIEW, PLANS_MANAGE, RUNS_VIEW, RUNS_EXECUTE, RUNS_ABORT,
        FILES_VIEW, FILES_VIEW_CONTENTS, USERS_MANAGE, USERS_RESET_2FA,
        SYSTEM_SETTINGS, TOKENS_MANAGE, NOTIFICATIONS_MANAGE,
    ])


@pytest.fixture
def operator_user():
    """Operator user with tokens.manage persisted in DB."""
    from app.auth.permissions import TOKENS_MANAGE, NOTIFICATIONS_MANAGE
    return _seed_user_with_profile([TOKENS_MANAGE, NOTIFICATIONS_MANAGE])


@pytest.fixture
def no_token_user():
    """User WITHOUT tokens.manage persisted in DB."""
    from app.auth.permissions import CONNECTIONS_VIEW
    return _seed_user_with_profile([CONNECTIONS_VIEW])


# ── Helper to make a request ──────────────────────────────────────────────────


def _request(
    client,
    user: User,
    method: str,
    path: str,
    *,
    auth_method: str = "session",
    json: dict | None = None,
):
    """Make a request as the given user, simulating session or PAT auth.

    Uses dependency_overrides for both get_current_user (general auth gate)
    and require_session_auth (session-only gate for POST/DELETE tokens).

    When auth_method='pat', require_session_auth raises 403 session_required.
    When auth_method='session', it passes through.
    """
    from app.auth.permissions import require_session_auth
    from fastapi import HTTPException, status as http_status

    app.dependency_overrides[get_current_user] = _override_user(user)

    if auth_method == "pat":
        async def _session_dep_pat():
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "session_required",
                    "detail": "This endpoint requires session authentication.",
                },
            )
        app.dependency_overrides[require_session_auth] = _session_dep_pat
    else:
        async def _session_dep_ok():
            return user
        app.dependency_overrides[require_session_auth] = _session_dep_ok

    try:
        fn = getattr(client, method.lower())
        kwargs: dict = {}
        if json is not None:
            kwargs["json"] = json
        return fn(path, **kwargs)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(require_session_auth, None)


# ── Tests: POST /api/me/tokens ────────────────────────────────────────────────


def test_create_token_returns_plaintext_once(client, admin_user):
    """POST /api/me/tokens: returns plaintext token in response (session auth)."""
    resp = _request(client, admin_user, "POST", "/api/me/tokens", json={"name": "CI pipeline"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "token" in body, "Response must include plaintext token"
    assert body["token"].startswith("sfbl_pat_"), "Token must start with sfbl_pat_"
    assert "token_hash" not in body, "token_hash must NOT appear in response"
    assert "id" in body
    assert body["name"] == "CI pipeline"
    assert body["prefix"] == "sfbl_pat_"
    assert len(body["last4"]) == 4
    assert body["revoked_at"] is None


def test_create_token_session_auth_201(client, operator_user):
    """Session-authenticated POST /api/me/tokens → 201."""
    resp = _request(
        client, operator_user, "POST", "/api/me/tokens",
        auth_method="session",
        json={"name": "My token"},
    )
    assert resp.status_code == 201, resp.text


def test_create_token_pat_auth_403(client, admin_user):
    """PAT-authenticated POST /api/me/tokens → 403 session_required."""
    resp = _request(
        client, admin_user, "POST", "/api/me/tokens",
        auth_method="pat",
        json={"name": "Should fail"},
    )
    assert resp.status_code == 403, resp.text
    detail = resp.json().get("detail", {})
    assert detail.get("error") == "session_required", f"Expected session_required, got: {detail}"


def test_create_token_no_tokens_manage_403(client, no_token_user):
    """POST without tokens.manage → 403."""
    resp = _request(
        client, no_token_user, "POST", "/api/me/tokens",
        auth_method="session",
        json={"name": "Bad"},
    )
    assert resp.status_code == 403, resp.text


def test_create_token_with_expiry(client, admin_user):
    """POST with expires_at creates a token with that expiry."""
    expiry = "2099-12-31T23:59:59+00:00"
    resp = _request(
        client, admin_user, "POST", "/api/me/tokens",
        auth_method="session",
        json={"name": "Expiring", "expires_at": expiry},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["expires_at"] is not None


def test_create_token_naive_expiry_normalised(client, admin_user):
    """A naive (no-offset) expires_at is normalised to UTC → 201, not a 500
    (Codex PR #99: naive datetime reached issue() and raised ValueError → 500)."""
    resp = _request(
        client, admin_user, "POST", "/api/me/tokens",
        auth_method="session",
        json={"name": "Naive expiry", "expires_at": "2099-12-31T23:59:59"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["expires_at"] is not None


def test_create_token_desktop_mode_blocked(client, admin_user, monkeypatch):
    """Desktop mode (auth_mode=none) blocks PAT creation with 403 — never a 500
    from inserting a PAT for the non-persisted virtual desktop user (Codex PR #99)."""
    from app.config import settings
    monkeypatch.setattr(settings, "auth_mode", "none")
    resp = _request(
        client, admin_user, "POST", "/api/me/tokens",
        auth_method="session",
        json={"name": "Desktop"},
    )
    assert resp.status_code == 403, resp.text
    assert "not available" in resp.text.lower()


# ── Tests: GET /api/me/tokens ─────────────────────────────────────────────────


def test_list_tokens_no_plaintext(client, admin_user):
    """GET /api/me/tokens: never returns plaintext or token_hash."""
    # First create a token
    create_resp = _request(
        client, admin_user, "POST", "/api/me/tokens",
        auth_method="session",
        json={"name": "Listed token"},
    )
    assert create_resp.status_code == 201, create_resp.text

    # Now list
    app.dependency_overrides[get_current_user] = _override_user(admin_user)
    try:
        list_resp = client.get("/api/me/tokens")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert list_resp.status_code == 200, list_resp.text
    items = list_resp.json()
    assert isinstance(items, list)
    assert len(items) >= 1
    for item in items:
        assert "token" not in item, "Plaintext token must not appear in list"
        assert "token_hash" not in item, "token_hash must not appear in list"
        assert "id" in item
        assert "name" in item
        assert "prefix" in item
        assert "last4" in item


def test_list_tokens_only_own(client, admin_user, operator_user):
    """GET /api/me/tokens: returns only the caller's tokens."""
    # Create a token for admin_user
    _request(
        client, admin_user, "POST", "/api/me/tokens",
        auth_method="session",
        json={"name": "Admin token"},
    )

    # List as operator_user — should see nothing
    app.dependency_overrides[get_current_user] = _override_user(operator_user)
    try:
        resp = client.get("/api/me/tokens")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_list_tokens_no_permission_403(client, no_token_user):
    """GET /api/me/tokens without tokens.manage → 403."""
    app.dependency_overrides[get_current_user] = _override_user(no_token_user)
    try:
        resp = client.get("/api/me/tokens")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 403, resp.text


# ── Tests: DELETE /api/me/tokens/{id} ────────────────────────────────────────


def test_revoke_own_token(client, admin_user):
    """DELETE /api/me/tokens/{id}: revokes caller's own token → 204."""
    create_resp = _request(
        client, admin_user, "POST", "/api/me/tokens",
        auth_method="session",
        json={"name": "To revoke"},
    )
    assert create_resp.status_code == 201, create_resp.text
    token_id = create_resp.json()["id"]

    del_resp = _request(
        client, admin_user, "DELETE", f"/api/me/tokens/{token_id}",
        auth_method="session",
    )
    assert del_resp.status_code == 204, del_resp.text


def test_revoke_token_pat_auth_403(client, admin_user):
    """PAT-authenticated DELETE → 403 session_required."""
    create_resp = _request(
        client, admin_user, "POST", "/api/me/tokens",
        auth_method="session",
        json={"name": "To revoke via PAT attempt"},
    )
    token_id = create_resp.json()["id"]

    del_resp = _request(
        client, admin_user, "DELETE", f"/api/me/tokens/{token_id}",
        auth_method="pat",
    )
    assert del_resp.status_code == 403, del_resp.text
    assert del_resp.json()["detail"]["error"] == "session_required"


def test_revoke_other_users_token_403(client, admin_user, operator_user):
    """DELETE: user cannot revoke another user's token → 403."""
    create_resp = _request(
        client, admin_user, "POST", "/api/me/tokens",
        auth_method="session",
        json={"name": "Owner token"},
    )
    token_id = create_resp.json()["id"]

    del_resp = _request(
        client, operator_user, "DELETE", f"/api/me/tokens/{token_id}",
        auth_method="session",
    )
    assert del_resp.status_code == 403, del_resp.text


def test_revoke_nonexistent_token_404(client, admin_user):
    """DELETE: non-existent token → 404."""
    del_resp = _request(
        client, admin_user, "DELETE", f"/api/me/tokens/{uuid.uuid4()}",
        auth_method="session",
    )
    assert del_resp.status_code == 404, del_resp.text


def test_revoke_idempotent(client, admin_user):
    """DELETE twice on same token is idempotent (second call also 204)."""
    create_resp = _request(
        client, admin_user, "POST", "/api/me/tokens",
        auth_method="session",
        json={"name": "Idempotent revoke"},
    )
    token_id = create_resp.json()["id"]

    for _ in range(2):
        resp = _request(
            client, admin_user, "DELETE", f"/api/me/tokens/{token_id}",
            auth_method="session",
        )
        assert resp.status_code == 204, resp.text


def test_revoke_without_permission_403(client, no_token_user):
    """DELETE without tokens.manage → 403."""
    resp = _request(
        client, no_token_user, "DELETE", f"/api/me/tokens/{uuid.uuid4()}",
        auth_method="session",
    )
    assert resp.status_code == 403, resp.text


# ── require_session_auth fail-closed semantics (Codex remediation) ─────────────


def _fake_request(auth_method) -> object:
    """Minimal stand-in for a Starlette Request with .state and .url.path."""
    from types import SimpleNamespace

    state = SimpleNamespace()
    if auth_method is not None:
        state.auth_method = auth_method
    return SimpleNamespace(state=state, url=SimpleNamespace(path="/api/me/tokens"))


@pytest.mark.parametrize(
    "auth_method, allowed",
    [
        ("session", True),   # explicit session auth → allowed
        ("pat", False),      # PAT → rejected (a token must not mint/revoke tokens)
        (None, False),       # unset → rejected (FAIL CLOSED)
        ("weird", False),    # any unrecognised value → rejected (FAIL CLOSED)
    ],
)
def test_require_session_auth_fails_closed(auth_method, allowed):
    """require_session_auth accepts ONLY auth_method == 'session'.

    Guards the interaction introduced when get_current_user's desktop branch
    began stamping auth_method='session': the gate now rejects any non-session
    value, including an unset one, so a future path that forgets to stamp it
    cannot silently bypass the session-only requirement.
    """
    from fastapi import HTTPException

    from app.auth.permissions import require_session_auth

    user = _seed_user_with_profile(["tokens.manage"])
    request = _fake_request(auth_method)

    if allowed:
        result = _run_async(require_session_auth(request=request, current_user=user))
        assert result is user
    else:
        with pytest.raises(HTTPException) as exc:
            _run_async(require_session_auth(request=request, current_user=user))
        assert exc.value.status_code == 403
        assert exc.value.detail["error"] == "session_required"
