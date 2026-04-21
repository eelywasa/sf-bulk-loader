"""Settings service package (SFBL-153).

Exposes the singleton SettingsService and the SETTINGS_REGISTRY.
"""

from app.services.settings.registry import SETTINGS_REGISTRY, SettingMeta
from app.services.settings.service import SettingsService, settings_service

__all__ = [
    "SETTINGS_REGISTRY",
    "SettingMeta",
    "SettingsService",
    "settings_service",
]
