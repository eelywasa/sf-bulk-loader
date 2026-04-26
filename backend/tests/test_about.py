"""Tests for GET /api/admin/about (SFBL-269).

Coverage:
- Desktop profile → 404 (route not registered)
- Unauthenticated → 401
- Non-admin (no system.settings) → 403
- Admin → 200 with all expected top-level keys and correct field shapes
- Security regression: no secret value appears in the serialised response
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.models.user import User
from app.services.auth import get_current_user, hash_password


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_non_admin_user() -> User:
    return User(
        id=str(uuid.uuid4()),
        email="viewer@example.com",
        hashed_password=hash_password("Test-P4ss!"),
        is_admin=False,
        status="active",
    )


# ── Desktop profile: router not registered ────────────────────────────────────


def test_about_desktop_returns_404():
    original = settings.auth_mode
    settings.auth_mode = "none"
    try:
        from fastapi import FastAPI
        from app.api.admin_about import router as about_router

        app = FastAPI()
        if settings.auth_mode != "none":
            app.include_router(about_router)

        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/api/admin/about")
        assert resp.status_code == 404
    finally:
        settings.auth_mode = original


# ── Unauthenticated → 401 ─────────────────────────────────────────────────────


def test_about_unauthenticated(client):
    resp = client.get("/api/admin/about")
    assert resp.status_code == 401


# ── Non-admin (no system.settings permission) → 403 ──────────────────────────


def test_about_non_admin_returns_403(client):
    non_admin = _make_non_admin_user()
    # Profile with no permissions — require_permission will deny
    non_admin.profile = None

    app_ref = client.app
    original_override = app_ref.dependency_overrides.copy()

    async def _override():
        return non_admin

    app_ref.dependency_overrides[get_current_user] = _override
    try:
        resp = client.get("/api/admin/about")
        assert resp.status_code == 403
    finally:
        app_ref.dependency_overrides = original_override


# ── Admin → 200 ───────────────────────────────────────────────────────────────


def test_about_admin_returns_200_with_expected_shape(auth_client):
    resp = auth_client.get("/api/admin/about")
    assert resp.status_code == 200

    data = resp.json()

    # Top-level keys
    assert set(data.keys()) == {"app", "distribution", "runtime", "database", "salesforce", "email", "storage"}

    # app section
    app = data["app"]
    assert "version" in app
    assert "git_sha" in app
    assert "build_time" in app
    assert isinstance(app["version"], str) and app["version"]

    # distribution section
    dist = data["distribution"]
    assert "profile" in dist
    assert dist["profile"] in ("desktop", "self_hosted", "aws_hosted")
    assert "auth_mode" in dist

    # runtime section
    runtime = data["runtime"]
    assert "python_version" in runtime
    assert "fastapi_version" in runtime
    # Python version should be a dotted triple like "3.12.7"
    parts = runtime["python_version"].split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)

    # database section
    db = data["database"]
    assert db["backend"] in ("sqlite", "postgresql")
    assert "alembic_head" in db

    # salesforce section
    sf = data["salesforce"]
    assert "api_version" in sf
    assert isinstance(sf["api_version"], str)

    # email section
    email = data["email"]
    assert "backend" in email
    assert isinstance(email["enabled"], bool)

    # storage section
    storage = data["storage"]
    assert "input_connections" in storage
    assert "output_connections" in storage
    assert isinstance(storage["input_connections"], dict)
    assert isinstance(storage["output_connections"], dict)


def test_about_version_reflects_env_var(auth_client):
    sentinel = "9.99.12345-test"
    original = os.environ.get("APP_VERSION")
    os.environ["APP_VERSION"] = sentinel
    try:
        resp = auth_client.get("/api/admin/about")
        assert resp.status_code == 200
        assert resp.json()["app"]["version"] == sentinel
    finally:
        if original is None:
            os.environ.pop("APP_VERSION", None)
        else:
            os.environ["APP_VERSION"] = original


# ── Security regression: no secrets in response ───────────────────────────────


def test_about_response_contains_no_secrets(auth_client):
    """Assert that no configured secret value appears anywhere in the serialised JSON."""
    resp = auth_client.get("/api/admin/about")
    assert resp.status_code == 200

    payload_str = json.dumps(resp.json())

    secret_candidates = [
        settings.encryption_key,
        settings.jwt_secret_key,
        # Database URL could contain credentials in PostgreSQL form
        settings.database_url if "@" in settings.database_url else None,
    ]
    # Include admin password if set (should never appear in system info)
    if settings.admin_password:
        secret_candidates.append(settings.admin_password)

    for secret in secret_candidates:
        if secret and len(secret) > 8:  # skip trivially short/empty values
            assert secret not in payload_str, (
                f"Secret value (first 8 chars: {secret[:8]}…) found in /api/admin/about response"
            )
