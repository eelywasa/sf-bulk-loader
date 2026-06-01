"""Tests for discovery.py — OS-path resolution, fixture parsing, error cases."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from sf_bulk_loader_mcp.discovery import (
    DiscoveryFileNotFoundError,
    DiscoveryFileMalformedError,
    DiscoveryFile,
    discovery_file_path,
    read_discovery,
    resolve_base_url,
    _data_dir,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ── OS data-dir path resolution ───────────────────────────────────────────────

class TestDataDirPaths:
    """Verify _data_dir returns the correct OS-convention path per platform."""

    def test_macos_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("HOME", str(tmp_path))
        # Reset Path.home() cache by patching directly
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        result = _data_dir("My App")
        assert result == tmp_path / "Library" / "Application Support" / "My App"

    def test_linux_xdg_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        result = _data_dir("My App")
        assert result == tmp_path / "xdg" / "My App"

    def test_linux_fallback_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        result = _data_dir("My App")
        assert result == tmp_path / ".config" / "My App"

    def test_windows_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
        result = _data_dir("My App")
        assert result == tmp_path / "roaming" / "My App"

    def test_windows_fallback_no_appdata(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("APPDATA", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        result = _data_dir("My App")
        assert result == tmp_path / "AppData" / "Roaming" / "My App"

    def test_default_app_name_matches_electron_product_name(self) -> None:
        """The default app_name must match the Electron productName."""
        path = discovery_file_path()
        # productName from electron-builder.config.js is "Salesforce Bulk Loader"
        assert "Salesforce Bulk Loader" in str(path)
        assert path.name == "mcp-discovery.json"


# ── Fixture file parsing ──────────────────────────────────────────────────────

class TestReadDiscovery:
    """Test read_discovery() against real and synthetic fixture files."""

    def test_reads_valid_fixture(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fixture = FIXTURES_DIR / "mcp-discovery.json"
        monkeypatch.setattr(
            "sf_bulk_loader_mcp.discovery.discovery_file_path",
            lambda app_name="Salesforce Bulk Loader": fixture,
        )
        result = read_discovery()
        assert isinstance(result, DiscoveryFile)
        assert result.schema_version == 1
        assert result.base_url == "http://127.0.0.1:47000"
        assert result.port == 47000
        assert result.pid == 12345

    def test_missing_file_raises_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        absent = tmp_path / "mcp-discovery.json"
        monkeypatch.setattr(
            "sf_bulk_loader_mcp.discovery.discovery_file_path",
            lambda app_name="Salesforce Bulk Loader": absent,
        )
        with pytest.raises(DiscoveryFileNotFoundError) as exc_info:
            read_discovery()
        assert "mcp-discovery.json" in str(exc_info.value)
        assert "running" in str(exc_info.value).lower()

    def test_malformed_json_raises_malformed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bad_file = tmp_path / "mcp-discovery.json"
        bad_file.write_text("not valid json", encoding="utf-8")
        monkeypatch.setattr(
            "sf_bulk_loader_mcp.discovery.discovery_file_path",
            lambda app_name="Salesforce Bulk Loader": bad_file,
        )
        with pytest.raises(DiscoveryFileMalformedError) as exc_info:
            read_discovery()
        assert "mcp-discovery.json" in str(exc_info.value)

    def test_missing_required_field_raises_malformed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bad_file = tmp_path / "mcp-discovery.json"
        # Missing 'base_url'
        bad_file.write_text(json.dumps({"schema_version": 1, "port": 47000, "pid": 99}), encoding="utf-8")
        monkeypatch.setattr(
            "sf_bulk_loader_mcp.discovery.discovery_file_path",
            lambda app_name="Salesforce Bulk Loader": bad_file,
        )
        with pytest.raises(DiscoveryFileMalformedError):
            read_discovery()

    def test_extra_fields_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        good_file = tmp_path / "mcp-discovery.json"
        good_file.write_text(
            json.dumps({
                "schema_version": 1,
                "base_url": "http://127.0.0.1:47001",
                "port": 47001,
                "pid": 42,
                "extra_future_field": "ignored",
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "sf_bulk_loader_mcp.discovery.discovery_file_path",
            lambda app_name="Salesforce Bulk Loader": good_file,
        )
        result = read_discovery()
        assert result.port == 47001


# ── resolve_base_url ──────────────────────────────────────────────────────────

class TestResolveBaseUrl:
    """Explicit URL always wins; discovery is called when not provided."""

    def test_explicit_url_wins(self) -> None:
        url = resolve_base_url(explicit_url="http://localhost:9999/")
        assert url == "http://localhost:9999"  # trailing slash stripped

    def test_explicit_url_strips_trailing_slash(self) -> None:
        url = resolve_base_url(explicit_url="http://example.com/api///")
        assert not url.endswith("/")

    def test_falls_through_to_discovery(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fixture = FIXTURES_DIR / "mcp-discovery.json"
        monkeypatch.setattr(
            "sf_bulk_loader_mcp.discovery.discovery_file_path",
            lambda app_name="Salesforce Bulk Loader": fixture,
        )
        url = resolve_base_url()
        assert url == "http://127.0.0.1:47000"

    def test_missing_discovery_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        absent = tmp_path / "mcp-discovery.json"
        monkeypatch.setattr(
            "sf_bulk_loader_mcp.discovery.discovery_file_path",
            lambda app_name="Salesforce Bulk Loader": absent,
        )
        with pytest.raises(DiscoveryFileNotFoundError):
            resolve_base_url()
