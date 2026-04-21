"""SETTINGS_REGISTRY — central registry of all DB-backed tunable settings (SFBL-153).

Each entry maps a setting key to a SettingMeta dataclass describing its type,
default value, optional env-var override, and whether the value is stored
encrypted at rest.

Wave S1 seeds the registry with the 8 security/lockout integer keys introduced
by Epic A (SFBL-186).  Later waves (SFBL-154/155/156/157) will add email,
Salesforce, partitioning, and auth-tuning keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class SettingMeta:
    """Metadata for a single DB-backed setting."""

    key: str
    category: str
    type: Literal["str", "int", "bool", "float"]
    default: Any
    is_secret: bool = False
    description: str = ""
    env_var: str | None = None
    restart_required: bool = False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SETTINGS_REGISTRY: dict[str, SettingMeta] = {}


def _register(*metas: SettingMeta) -> None:
    for meta in metas:
        if meta.key in SETTINGS_REGISTRY:
            raise ValueError(f"Duplicate setting key in registry: {meta.key!r}")
        SETTINGS_REGISTRY[meta.key] = meta


# ---------------------------------------------------------------------------
# Security / login lockout  (category="security")
# ---------------------------------------------------------------------------

_register(
    SettingMeta(
        key="login_rate_limit_attempts",
        category="security",
        type="int",
        default=20,
        description="Maximum login attempts allowed within the rate-limit window before throttling.",
        env_var="LOGIN_RATE_LIMIT_ATTEMPTS",
    ),
    SettingMeta(
        key="login_rate_limit_window_seconds",
        category="security",
        type="int",
        default=300,
        description="Window size in seconds for the login rate-limit counter.",
        env_var="LOGIN_RATE_LIMIT_WINDOW_SECONDS",
    ),
    SettingMeta(
        key="login_tier1_threshold",
        category="security",
        type="int",
        default=5,
        description="Number of consecutive failures that trigger a Tier-1 temporary lockout.",
        env_var="LOGIN_TIER1_THRESHOLD",
    ),
    SettingMeta(
        key="login_tier1_window_minutes",
        category="security",
        type="int",
        default=15,
        description="Rolling window in minutes over which Tier-1 failures are counted.",
        env_var="LOGIN_TIER1_WINDOW_MINUTES",
    ),
    SettingMeta(
        key="login_tier1_lock_minutes",
        category="security",
        type="int",
        default=15,
        description="Duration in minutes for a Tier-1 automatic account lock.",
        env_var="LOGIN_TIER1_LOCK_MINUTES",
    ),
    SettingMeta(
        key="login_tier2_threshold",
        category="security",
        type="int",
        default=15,
        description="Total failures within tier2_window_hours that trigger a permanent Tier-2 lock.",
        env_var="LOGIN_TIER2_THRESHOLD",
    ),
    SettingMeta(
        key="login_tier2_tier1_count",
        category="security",
        type="int",
        default=3,
        description="Number of Tier-1 locks within tier2_window_hours that trigger a Tier-2 lock.",
        env_var="LOGIN_TIER2_TIER1_COUNT",
    ),
    SettingMeta(
        key="login_tier2_window_hours",
        category="security",
        type="int",
        default=24,
        description="Rolling window in hours over which Tier-2 lock triggers are counted.",
        env_var="LOGIN_TIER2_WINDOW_HOURS",
    ),
)
