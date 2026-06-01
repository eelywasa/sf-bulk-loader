"""Tests for config.py — auth mode, URL resolution precedence, validation."""

from __future__ import annotations

import pytest

from sf_bulk_loader_mcp.config import AuthMode, McpSettings


class TestAuthModeDefaults:
    def test_default_auth_mode_is_none(self) -> None:
        settings = McpSettings()
        assert settings.auth_mode == AuthMode.NONE

    def test_none_mode_allows_missing_base_url(self) -> None:
        settings = McpSettings(auth_mode="none")
        assert settings.bulkloader_base_url is None


class TestPatModeValidation:
    def test_pat_mode_requires_base_url(self) -> None:
        from pydantic import ValidationError
        with pytest.raises((ValueError, ValidationError)):
            McpSettings(auth_mode="pat", bulkloader_base_url=None)

    def test_pat_mode_with_base_url_ok(self) -> None:
        settings = McpSettings(auth_mode="pat", bulkloader_base_url="http://example.com")
        assert settings.auth_mode == AuthMode.PAT
        assert settings.bulkloader_base_url == "http://example.com"


class TestBaseUrlPrecedence:
    def test_explicit_url_env_var_strips_slash(self) -> None:
        settings = McpSettings(bulkloader_base_url="http://localhost:8000/")
        assert settings.bulkloader_base_url == "http://localhost:8000"

    def test_base_url_none_by_default(self) -> None:
        settings = McpSettings()
        assert settings.bulkloader_base_url is None

    def test_base_url_set_via_field(self) -> None:
        settings = McpSettings(bulkloader_base_url="http://custom:9999")
        assert settings.bulkloader_base_url == "http://custom:9999"


class TestAppNameDefault:
    def test_default_app_name_matches_electron_app_name(self) -> None:
        settings = McpSettings()
        # Must match Electron's app.getName() — i.e. electron/package.json `name`
        # (what app.getPath('userData') uses), NOT the electron-builder productName.
        assert settings.bulkloader_app_name == "sf-bulk-loader-desktop"

    def test_app_name_overridable(self) -> None:
        settings = McpSettings(bulkloader_app_name="My Custom App")
        assert settings.bulkloader_app_name == "My Custom App"


class TestEnvVarLoading:
    def test_auth_mode_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_MODE", "none")
        settings = McpSettings()
        assert settings.auth_mode == AuthMode.NONE

    def test_base_url_from_env_overrides_discovery(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BULKLOADER_BASE_URL", "http://from-env:1234")
        settings = McpSettings()
        assert settings.bulkloader_base_url == "http://from-env:1234"
