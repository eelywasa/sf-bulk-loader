"""MCP server configuration via pydantic-settings.

Auth modes:
  none  — desktop profile; backend URL is resolved from the Electron-written
          mcp-discovery.json file (see discovery.py). No auth header needed.
  pat   — remote / hosted profile (SFBL-371); caller must supply BULKLOADER_PAT.
          BULKLOADER_BASE_URL is required in this mode.

URL resolution precedence:
  1. BULKLOADER_BASE_URL env var (dev override, always wins).
  2. pat mode  → required; validation fails if not set.
  3. none mode → resolved at runtime from the discovery file (lazy; not set
                 at config-load time, resolved by the client).
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthMode(str, Enum):
    """Deployment / auth mode for the MCP server."""

    NONE = "none"   # desktop — no auth header
    PAT = "pat"     # remote / hosted — Bearer token (SFBL-371)


class McpSettings(BaseSettings):
    """Top-level configuration for the MCP server.

    All fields are read from environment variables (prefix-free) or from a
    .env file in the working directory.

    Environment variables:
        AUTH_MODE              — "none" (default) or "pat".
        BULKLOADER_BASE_URL    — Base URL for the Salesforce Bulk Loader API
                                 (e.g. http://localhost:8000).  Required when
                                 AUTH_MODE=pat.  Optional when AUTH_MODE=none;
                                 when supplied it overrides discovery-file
                                 resolution (useful for local dev).
        BULKLOADER_PAT         — Personal Access Token.  Required when
                                 AUTH_MODE=pat (SFBL-371 wires this up).
        BULKLOADER_APP_NAME    — Override the Electron productName used to
                                 locate the OS data dir (default:
                                 "Salesforce Bulk Loader").
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    auth_mode: AuthMode = AuthMode.NONE

    # Explicit URL override — always wins for local dev.
    bulkloader_base_url: Optional[str] = None

    # Personal Access Token for PAT mode (SFBL-371).
    # Required when auth_mode == "pat"; validated by model_validator below.
    # NEVER log this value — honour sanitization rules.
    bulkloader_pat: Optional[str] = None

    # App name used by discovery.py to locate the OS data dir. This MUST be the
    # Electron app.getName() value — i.e. electron/package.json `name`
    # ("sf-bulk-loader-desktop") — which is what app.getPath('userData') uses.
    # NOT the electron-builder productName ("Salesforce Bulk Loader"), which only
    # names the .app/.dmg artifact. Override via BULKLOADER_APP_NAME if needed.
    bulkloader_app_name: str = "sf-bulk-loader-desktop"

    @field_validator("bulkloader_base_url", mode="before")
    @classmethod
    def strip_trailing_slash(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return v.rstrip("/")
        return v

    @model_validator(mode="after")
    def validate_pat_mode_requirements(self) -> "McpSettings":
        """Enforce that PAT mode has both a base URL and a token configured."""
        if self.auth_mode == AuthMode.PAT:
            if not self.bulkloader_base_url:
                raise ValueError(
                    "BULKLOADER_BASE_URL is required when AUTH_MODE=pat. "
                    "Set it to the base URL of your hosted Bulk Loader instance "
                    "(e.g. https://bulkloader.example.com)."
                )
            if not self.bulkloader_pat:
                raise ValueError(
                    "BULKLOADER_PAT is required when AUTH_MODE=pat. "
                    "Mint a Personal Access Token in the Bulk Loader UI "
                    "(Settings → API Tokens) and set it here."
                )
        return self
