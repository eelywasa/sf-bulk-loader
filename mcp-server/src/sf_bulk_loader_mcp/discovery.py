"""Desktop backend discovery via OS-convention data directories.

The Electron app (SFBL-364) writes ``mcp-discovery.json`` into the platform
data directory when the backend starts.  This module locates that file using
OS conventions — critically WITHOUT relying on Electron's ``app.getPath()``,
because the MCP server runs as a frozen PyInstaller binary that has no Electron
context.

OS conventions (must match Electron's app.getPath('userData') for productName
"Salesforce Bulk Loader"):
  macOS   ~/Library/Application Support/Salesforce Bulk Loader/
  Linux   $XDG_CONFIG_HOME/Salesforce Bulk Loader/   (or ~/.config/…)
  Windows %APPDATA%\\Salesforce Bulk Loader\\

The app_name used to construct the path defaults to "Salesforce Bulk Loader"
(the Electron ``productName`` from electron-builder.config.js) and is
overridable via the ``BULKLOADER_APP_NAME`` environment variable or the
``McpSettings.bulkloader_app_name`` field so tests can point at a temp dir.

Discovery-file schema (written by SFBL-364):
  {
    "schema_version": 1,
    "base_url": "http://127.0.0.1:<port>",
    "port": <int>,
    "pid": <int>
  }

Errors are structured and never swallowed silently.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ValidationError


# ── Discovery-file schema ────────────────────────────────────────────────────

class DiscoveryFile(BaseModel):
    """Schema for the mcp-discovery.json written by the Electron main process."""

    schema_version: int
    base_url: str
    port: int
    pid: int


# ── Public errors ────────────────────────────────────────────────────────────

class DiscoveryError(RuntimeError):
    """Raised when the discovery file cannot be found or parsed."""


class DiscoveryFileNotFoundError(DiscoveryError):
    """The mcp-discovery.json file does not exist (backend not running?)."""


class DiscoveryFileMalformedError(DiscoveryError):
    """The mcp-discovery.json file exists but cannot be parsed."""


# ── Path resolution ───────────────────────────────────────────────────────────

def _data_dir(app_name: str) -> Path:
    """Return the OS-convention user data directory for *app_name*.

    Matches Electron's ``app.getPath('userData')`` for the same app name on
    each platform.  On Linux we respect XDG_CONFIG_HOME.
    """
    platform = sys.platform

    if platform == "darwin":
        # macOS: ~/Library/Application Support/<appName>
        return Path.home() / "Library" / "Application Support" / app_name

    if platform == "win32":
        # Windows: %APPDATA%\<appName>
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / app_name
        # Fallback if APPDATA is somehow unset.
        return Path.home() / "AppData" / "Roaming" / app_name

    # Linux / other POSIX: XDG_CONFIG_HOME or ~/.config/<appName>
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / app_name
    return Path.home() / ".config" / app_name


def discovery_file_path(app_name: str = "Salesforce Bulk Loader") -> Path:
    """Return the full path to ``mcp-discovery.json`` for *app_name*."""
    return _data_dir(app_name) / "mcp-discovery.json"


# ── Public API ────────────────────────────────────────────────────────────────

def read_discovery(app_name: str = "Salesforce Bulk Loader") -> DiscoveryFile:
    """Read and validate the ``mcp-discovery.json`` written by Electron.

    Raises:
        DiscoveryFileNotFoundError: The file does not exist (backend not running,
            or the app has not been launched yet).
        DiscoveryFileMalformedError: The file exists but is not valid JSON or
            does not match the expected schema.
    """
    path = discovery_file_path(app_name)

    if not path.exists():
        raise DiscoveryFileNotFoundError(
            f"mcp-discovery.json not found at {path!s}. "
            "Is the Salesforce Bulk Loader desktop app running?"
        )

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise DiscoveryFileMalformedError(
            f"Failed to read mcp-discovery.json at {path!s}: {exc}"
        ) from exc

    try:
        return DiscoveryFile(**data)
    except (ValidationError, TypeError) as exc:
        raise DiscoveryFileMalformedError(
            f"mcp-discovery.json at {path!s} has unexpected content: {exc}"
        ) from exc


def resolve_base_url(
    explicit_url: Optional[str] = None,
    app_name: str = "Salesforce Bulk Loader",
) -> str:
    """Return the backend base URL, using the explicit override or discovery.

    Args:
        explicit_url: If set (e.g. from ``BULKLOADER_BASE_URL``), this always
            wins — useful for local dev and PAT mode.
        app_name:     Electron productName; used to locate the data dir.

    Returns:
        Bare base URL with no trailing slash, e.g. ``"http://127.0.0.1:47123"``.

    Raises:
        DiscoveryFileNotFoundError / DiscoveryFileMalformedError if the
        discovery file is needed but absent or broken.
    """
    if explicit_url:
        return explicit_url.rstrip("/")

    discovery = read_discovery(app_name)
    return discovery.base_url.rstrip("/")
