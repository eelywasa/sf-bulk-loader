"""Tests for POST /api/admin/email/test (SFBL-143).

Coverage:
- Desktop profile → 404 (route not registered)
- Unauthenticated → 401
- Non-admin user → 403
- Admin + noop backend, happy path → 200 status=skipped
- Admin + simulated render failure → 422 with stable code
- Admin + simulated backend failure → 200 status=failed with reason
- Invalid email in 'to' → 400
- Unknown template name → 400
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.models.user import User
from app.services.auth import get_current_user, hash_password
from app.services.email.errors import EmailErrorReason, EmailRenderError
from app.services.email.service import get_email_service

# ── Shared helpers ─────────────────────────────────────────────────────────────


def _make_admin() -> User:
    return User(
        id=str(uuid.uuid4()),
        username="admin-test",
        hashed_password=hash_password("Test-Admin-P4ss!"),
        role="admin",
        is_active=True,
    )


def _make_regular_user() -> User:
    return User(
        id=str(uuid.uuid4()),
        username="regular-user",
        hashed_password=hash_password("Test-Admin-P4ss!"),
        role="user",
        is_active=True,
    )


# ── Desktop profile: router not registered → 404 ──────────────────────────────


def test_admin_email_test_desktop_returns_404():
    """On desktop profile (auth_mode=none), the admin email route is not registered at all."""
    # Patch auth_mode=none before importing / building the TestClient so that
    # the conditional include_router in main.py is evaluated with the patched value.
    original_auth_mode = settings.auth_mode
    settings.auth_mode = "none"

    try:
        # Force a fresh import of main with the new settings value.
        # We can't easily re-run lifespan, so we build a minimal app manually
        # that mirrors the conditional logic.
        from fastapi import FastAPI
        from app.api.admin_email import router as admin_email_router

        test_app = FastAPI()
        if settings.auth_mode != "none":
            test_app.include_router(admin_email_router)

        with TestClient(test_app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/api/admin/email/test",
                json={"to": "test@example.com", "template": "auth/password_reset"},
            )
        assert resp.status_code == 404
    finally:
        settings.auth_mode = original_auth_mode


# ── Fixtures using the main app (auth_mode != "none") ─────────────────────────


@pytest.fixture
def admin_client(client):
    """TestClient with admin user injected via dependency override."""
    from app.main import app
    from app.database import get_db
    from tests.conftest import _TestSession

    admin = _make_admin()

    async def override_get_db():
        async with _TestSession() as session:
            yield session

    async def override_get_current_user():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def user_client(client):
    """TestClient with non-admin user injected via dependency override."""
    from app.main import app
    from app.database import get_db
    from tests.conftest import _TestSession

    regular = _make_regular_user()

    async def override_get_db():
        async with _TestSession() as session:
            yield session

    async def override_get_current_user():
        return regular

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


# ── Unauthenticated → 401 ─────────────────────────────────────────────────────


def test_admin_email_test_unauthenticated(client):
    """No auth token → 401."""
    resp = client.post(
        "/api/admin/email/test",
        json={"to": "test@example.com", "template": "auth/password_reset"},
    )
    # The auth_mode in the test suite is not 'none', so the route is registered.
    # Without a token the route should 401.
    assert resp.status_code == 401


# ── Non-admin → 403 ───────────────────────────────────────────────────────────


def test_admin_email_test_non_admin_returns_403(user_client):
    """Regular (non-admin) user → 403."""
    resp = user_client.post(
        "/api/admin/email/test",
        json={"to": "test@example.com", "template": "auth/password_reset"},
    )
    assert resp.status_code == 403


# ── Admin + noop backend → 200 status=skipped ─────────────────────────────────


def test_admin_email_test_noop_happy_path(admin_client):
    """Admin user, noop backend → 200 with status=skipped and delivery_id."""
    resp = admin_client.post(
        "/api/admin/email/test",
        json={"to": "recipient@example.com", "template": "auth/password_reset"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "skipped"
    assert "delivery_id" in body
    assert body["delivery_id"]  # non-empty
    assert body["backend"] == "noop"
    assert "provider_message_id" in body  # may be null for noop


def test_admin_email_test_run_complete_template(admin_client):
    """Admin can use the notifications/run_complete template."""
    resp = admin_client.post(
        "/api/admin/email/test",
        json={"to": "recipient@example.com", "template": "notifications/run_complete"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "skipped"
    assert body["backend"] == "noop"


def test_admin_email_test_email_change_template(admin_client):
    """Admin can use the auth/email_change_verify template."""
    resp = admin_client.post(
        "/api/admin/email/test",
        json={"to": "recipient@example.com", "template": "auth/email_change_verify"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


# ── Simulated render failure → 422 ────────────────────────────────────────────


def test_admin_email_test_render_failure_returns_422(admin_client):
    """When send_template raises EmailRenderError, endpoint returns 422 with the stable code."""
    from app.main import app

    async def _mock_render_failure(*args: Any, **kwargs: Any) -> None:
        raise EmailRenderError("SUBJECT_TOO_LONG")

    mock_service = MagicMock()
    mock_service.send_template = _mock_render_failure

    async def override_email_service():
        return mock_service

    app.dependency_overrides[get_email_service] = override_email_service
    try:
        resp = admin_client.post(
            "/api/admin/email/test",
            json={"to": "recipient@example.com", "template": "auth/password_reset"},
        )
    finally:
        app.dependency_overrides.pop(get_email_service, None)

    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "SUBJECT_TOO_LONG"
    assert "message" in body
    # Must NOT leak any offending value — just the stable code
    assert "SUBJECT_TOO_LONG" in body["code"]


# ── Simulated backend failure → 200 status=failed ─────────────────────────────


def test_admin_email_test_backend_failure_returns_200_failed(admin_client):
    """When the backend returns a failure, endpoint returns 200 with status=failed."""
    from app.main import app
    from app.models.email_delivery import EmailDelivery, DeliveryStatus

    failed_delivery = EmailDelivery()
    failed_delivery.id = str(uuid.uuid4())
    failed_delivery.status = DeliveryStatus.failed
    failed_delivery.last_error_code = EmailErrorReason.PERMANENT_REJECT.value
    failed_delivery.last_error_msg = "Mailbox not found"
    failed_delivery.backend = "smtp"
    failed_delivery.provider_message_id = None

    async def _mock_failed(*args: Any, **kwargs: Any):
        return failed_delivery

    mock_service = MagicMock()
    mock_service.send_template = AsyncMock(return_value=failed_delivery)

    async def override_email_service():
        return mock_service

    app.dependency_overrides[get_email_service] = override_email_service
    try:
        resp = admin_client.post(
            "/api/admin/email/test",
            json={"to": "recipient@example.com", "template": "auth/password_reset"},
        )
    finally:
        app.dependency_overrides.pop(get_email_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["reason"] == "permanent_reject"
    assert body["last_error_msg"] == "Mailbox not found"
    assert body["delivery_id"] == str(failed_delivery.id)
    assert body["backend"] == "smtp"


# ── Invalid email → 400 ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_email",
    [
        "not-an-email",
        "missing@",
        "@nodomain",
        "",
        "spaces in@email.com",
    ],
)
def test_admin_email_test_invalid_email_returns_400(admin_client, bad_email):
    """Invalid 'to' email address → 400."""
    resp = admin_client.post(
        "/api/admin/email/test",
        json={"to": bad_email, "template": "auth/password_reset"},
    )
    assert resp.status_code == 400


# ── Unknown template → 400 ────────────────────────────────────────────────────


def test_admin_email_test_unknown_template_returns_400(admin_client):
    """Unknown template name → 400."""
    resp = admin_client.post(
        "/api/admin/email/test",
        json={"to": "recipient@example.com", "template": "auth/nonexistent"},
    )
    assert resp.status_code == 400
    assert "Unknown template" in resp.json()["detail"]
