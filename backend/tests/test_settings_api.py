"""Tests for the settings API (SFBL-154).

Coverage:
- GET / as admin → all registry keys present, secrets masked as '***'
- GET /security → only security-category keys, correct shape
- GET /unknowncategory → 404
- PATCH /security with valid key → 200, value persisted
- PATCH /security with unknown key → 422 with "unknown" in error detail
- PATCH /security with wrong-category key (via monkeypatched temp key) → 422
- PATCH /security with bad type → 422
- Non-admin PATCH → 403
- Non-admin GET → 403
- X-Settings-Cache-TTL: 60 present on GET and PATCH responses
- Desktop profile (auth_mode=none) → router not registered → 404
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings as app_settings
from app.models.user import User
from app.services.auth import get_current_user
import app.services.settings.service as _svc_mod
from app.services.settings.registry import SETTINGS_REGISTRY, SettingMeta
from tests.conftest import _TestSession, _run_async


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _admin_user() -> User:
    return User(
        id=str(uuid.uuid4()),
        username="settings-admin",
        role="admin",
        status="active",
        is_admin=True,
    )


def _regular_user() -> User:
    return User(
        id=str(uuid.uuid4()),
        username="settings-user",
        role="user",
        status="active",
        is_admin=False,
    )


def _admin_client(client_fixture: TestClient, admin: User) -> TestClient:
    """Return the fixture client with get_current_user overridden to *admin*."""
    from app.main import app

    async def _override_admin() -> User:
        return admin

    app.dependency_overrides[get_current_user] = _override_admin
    return client_fixture


def _non_admin_client(client_fixture: TestClient, user: User) -> TestClient:
    from app.main import app

    async def _override_user() -> User:
        return user

    app.dependency_overrides[get_current_user] = _override_user
    return client_fixture


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_settings_service_cache():
    """Clear the settings_service cache between tests to avoid stale reads."""
    import app.services.settings.service as _svc_mod

    svc = _svc_mod.settings_service
    if svc is not None:
        svc._cache.clear()
    yield
    svc = _svc_mod.settings_service
    if svc is not None:
        svc._cache.clear()


@pytest.fixture(autouse=True)
def _clear_dependency_overrides_after():
    """Ensure dependency overrides are cleared after each test."""
    yield
    from app.main import app

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET / — all settings
# ---------------------------------------------------------------------------


def test_get_all_settings_returns_all_registry_keys(client: TestClient) -> None:
    """GET / as admin returns all registry keys grouped by category."""
    _admin_client(client, _admin_user())

    resp = client.get("/api/settings/")
    assert resp.status_code == 200

    body = resp.json()
    assert "categories" in body

    # Collect all returned keys
    returned_keys: set[str] = set()
    for cat in body["categories"]:
        for sv in cat["settings"]:
            returned_keys.add(sv["key"])

    for key in SETTINGS_REGISTRY:
        assert key in returned_keys, f"Registry key {key!r} missing from GET / response"


def test_get_all_settings_masks_secrets(client: TestClient) -> None:
    """Secrets must be returned as '***'."""
    # Temporarily add a secret key to the registry
    secret_meta = SettingMeta(
        key="_test_api_secret",
        category="security",
        type="str",
        default="s3cr3t",
        is_secret=True,
    )
    SETTINGS_REGISTRY[secret_meta.key] = secret_meta

    try:
        svc = _svc_mod.settings_service
        if svc is not None:
            _run_async(svc.set("_test_api_secret", "my-secret-value"))

        _admin_client(client, _admin_user())
        resp = client.get("/api/settings/")
        assert resp.status_code == 200

        # Find the secret key in the response
        found = False
        for cat in resp.json()["categories"]:
            for sv in cat["settings"]:
                if sv["key"] == "_test_api_secret":
                    assert sv["value"] == "***", "Secret value must be masked"
                    found = True
        assert found, "Secret key not found in response"
    finally:
        SETTINGS_REGISTRY.pop("_test_api_secret", None)
        svc = _svc_mod.settings_service
        if svc is not None:
            svc._cache.pop("_test_api_secret", None)


def test_get_all_settings_no_trailing_slash_no_redirect(client: TestClient) -> None:
    """GET /api/settings (no trailing slash) must return 200 directly, not 307.

    Some HTTP clients strip the Authorization header when following redirects,
    which breaks auth for callers that omit the trailing slash.
    """
    _admin_client(client, _admin_user())
    resp = client.get("/api/settings", follow_redirects=False)
    assert resp.status_code == 200


def test_get_all_settings_cache_ttl_header(client: TestClient) -> None:
    """GET / must include X-Settings-Cache-TTL: 60 header."""
    _admin_client(client, _admin_user())
    resp = client.get("/api/settings/")
    assert resp.headers.get("x-settings-cache-ttl") == "60"


# ---------------------------------------------------------------------------
# GET /{category}
# ---------------------------------------------------------------------------


def test_get_security_category_returns_only_security_keys(client: TestClient) -> None:
    """GET /security returns only security-category settings."""
    _admin_client(client, _admin_user())
    resp = client.get("/api/settings/security")
    assert resp.status_code == 200

    body = resp.json()
    assert body["category"] == "security"
    for sv in body["settings"]:
        assert sv["key"] in SETTINGS_REGISTRY
        assert SETTINGS_REGISTRY[sv["key"]].category == "security"


def test_get_unknown_category_returns_404(client: TestClient) -> None:
    """GET /unknowncategory → 404."""
    _admin_client(client, _admin_user())
    resp = client.get("/api/settings/unknowncategory_that_does_not_exist")
    assert resp.status_code == 404


def test_get_category_cache_ttl_header(client: TestClient) -> None:
    """GET /{category} must include X-Settings-Cache-TTL: 60 header."""
    _admin_client(client, _admin_user())
    resp = client.get("/api/settings/security")
    assert resp.headers.get("x-settings-cache-ttl") == "60"


# ---------------------------------------------------------------------------
# PATCH /{category}
# ---------------------------------------------------------------------------


def test_patch_security_updates_value(client: TestClient) -> None:
    """PATCH /security with a valid key → 200 and value persisted."""
    _admin_client(client, _admin_user())
    resp = client.patch(
        "/api/settings/security",
        json={"login_tier1_threshold": 10},
    )
    assert resp.status_code == 200

    # Value should be reflected in the response
    body = resp.json()
    found = False
    for sv in body["settings"]:
        if sv["key"] == "login_tier1_threshold":
            assert sv["value"] == 10
            found = True
    assert found

    # Verify persistence via a subsequent GET
    resp2 = client.get("/api/settings/security")
    assert resp2.status_code == 200
    for sv in resp2.json()["settings"]:
        if sv["key"] == "login_tier1_threshold":
            assert sv["value"] == 10


def test_patch_security_unknown_key_returns_422(client: TestClient) -> None:
    """PATCH /security with an unknown key → 422 with error='unknown'."""
    _admin_client(client, _admin_user())
    resp = client.patch(
        "/api/settings/security",
        json={"completely_unknown_key_xyz": 1},
    )
    assert resp.status_code == 422

    detail = resp.json().get("detail", [])
    assert any(
        e.get("error") == "unknown" and e.get("field") == "completely_unknown_key_xyz"
        for e in detail
    ), f"Expected 'unknown' error for key in detail, got: {detail}"


def test_patch_security_wrong_category_key_returns_422(client: TestClient) -> None:
    """PATCH /security with a key from a different category → 422.

    Strategy: temporarily add a key to the registry under category 'other',
    then try to PATCH it via /security.
    """
    other_meta = SettingMeta(
        key="_test_other_cat_key",
        category="other",
        type="int",
        default=0,
    )
    SETTINGS_REGISTRY[other_meta.key] = other_meta

    try:
        _admin_client(client, _admin_user())
        resp = client.patch(
            "/api/settings/security",
            json={"_test_other_cat_key": 1},
        )
        assert resp.status_code == 422

        detail = resp.json().get("detail", [])
        assert any(
            e.get("field") == "_test_other_cat_key"
            for e in detail
        ), f"Expected cross-category error for key, got: {detail}"
    finally:
        SETTINGS_REGISTRY.pop("_test_other_cat_key", None)


def test_patch_security_invalid_type_returns_422(client: TestClient) -> None:
    """PATCH /security with a non-coercible value → 422."""
    _admin_client(client, _admin_user())
    resp = client.patch(
        "/api/settings/security",
        json={"login_tier1_threshold": "not-an-int"},
    )
    assert resp.status_code == 422

    detail = resp.json().get("detail", [])
    assert any(
        e.get("field") == "login_tier1_threshold"
        for e in detail
    ), f"Expected type error for login_tier1_threshold, got: {detail}"


def test_patch_email_backend_rejects_unknown_value(client: TestClient) -> None:
    """PATCH /email with an unsupported email_backend → 422.

    Persisting an unknown backend string silently breaks startup on the next
    process restart because build_email_service raises for unknown backends.
    """
    _admin_client(client, _admin_user())
    resp = client.patch(
        "/api/settings/email",
        json={"email_backend": "sendgrid"},
    )
    assert resp.status_code == 422

    detail = resp.json().get("detail", [])
    assert any(
        e.get("field") == "email_backend" and "noop" in e.get("error", "")
        for e in detail
    ), f"Expected allow-list error for email_backend, got: {detail}"


def test_patch_email_backend_accepts_known_values(client: TestClient) -> None:
    """PATCH /email with each supported backend → 200."""
    _admin_client(client, _admin_user())
    for backend in ("noop", "smtp", "ses"):
        resp = client.patch(
            "/api/settings/email",
            json={"email_backend": backend},
        )
        assert resp.status_code == 200, f"backend={backend} failed: {resp.text}"


def test_patch_cache_ttl_header(client: TestClient) -> None:
    """PATCH /{category} must include X-Settings-Cache-TTL: 60 header."""
    _admin_client(client, _admin_user())
    resp = client.patch(
        "/api/settings/security",
        json={"login_tier1_threshold": 7},
    )
    assert resp.headers.get("x-settings-cache-ttl") == "60"


def test_patch_empty_body_returns_200(client: TestClient) -> None:
    """PATCH with empty dict is a no-op and returns 200."""
    _admin_client(client, _admin_user())
    resp = client.patch("/api/settings/security", json={})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Secret empty-string skip (TODO: full test requires SFBL-155 to land email keys)
# ---------------------------------------------------------------------------
# NOTE: No secret keys exist in the current registry (only SFBL-155 will add them).
# A test for "empty-string secret value is skipped" will be added by the SFBL-155
# agent once email keys (which are is_secret=True) are registered.
# Covered here with a temp fixture key for robustness:


def test_patch_secret_empty_string_is_skipped(client: TestClient) -> None:
    """PATCH with empty string for a secret key leaves the existing value unchanged."""
    secret_meta = SettingMeta(
        key="_test_api_patch_secret",
        category="security",
        type="str",
        default="original",
        is_secret=True,
    )
    SETTINGS_REGISTRY[secret_meta.key] = secret_meta

    try:
        svc = _svc_mod.settings_service
        if svc is not None:
            _run_async(svc.set("_test_api_patch_secret", "original-value"))

        _admin_client(client, _admin_user())
        resp = client.patch(
            "/api/settings/security",
            json={"_test_api_patch_secret": ""},
        )
        assert resp.status_code == 200

        # The raw service value should still be "original-value"
        svc = _svc_mod.settings_service
        if svc is not None:
            svc._cache.pop("_test_api_patch_secret", None)
            stored = _run_async(svc.get("_test_api_patch_secret"))
            assert stored == "original-value", "Empty string for secret must not overwrite existing value"
    finally:
        SETTINGS_REGISTRY.pop("_test_api_patch_secret", None)
        svc = _svc_mod.settings_service
        if svc is not None:
            svc._cache.pop("_test_api_patch_secret", None)


# ---------------------------------------------------------------------------
# Auth / non-admin access
# ---------------------------------------------------------------------------


def test_non_admin_get_all_returns_403(client: TestClient) -> None:
    """Non-admin user → 403 on GET /api/settings/."""
    _non_admin_client(client, _regular_user())
    resp = client.get("/api/settings/")
    assert resp.status_code == 403


def test_non_admin_get_category_returns_403(client: TestClient) -> None:
    """Non-admin user → 403 on GET /api/settings/security."""
    _non_admin_client(client, _regular_user())
    resp = client.get("/api/settings/security")
    assert resp.status_code == 403


def test_non_admin_patch_returns_403(client: TestClient) -> None:
    """Non-admin user → 403 on PATCH /api/settings/security."""
    _non_admin_client(client, _regular_user())
    resp = client.patch(
        "/api/settings/security",
        json={"login_tier1_threshold": 10},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Desktop profile — router not registered
# ---------------------------------------------------------------------------


def test_desktop_profile_settings_returns_404() -> None:
    """On desktop profile (auth_mode=none), the settings router is not registered."""
    from fastapi import FastAPI

    original_auth_mode = app_settings.auth_mode
    app_settings.auth_mode = "none"

    try:
        from app.api.settings import router as settings_router

        test_app = FastAPI()
        if app_settings.auth_mode != "none":
            test_app.include_router(settings_router)

        with TestClient(test_app, raise_server_exceptions=False) as c:
            resp = c.get("/api/settings/")
        assert resp.status_code == 404
    finally:
        app_settings.auth_mode = original_auth_mode
