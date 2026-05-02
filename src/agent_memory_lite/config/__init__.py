"""Configuration: settings, workspace metadata, local-only guard."""

from agent_memory_lite.config.local_only_guard import (
    LocalOnlyError,
    assert_local_only,
)
from agent_memory_lite.config.settings import Settings, get_settings

__all__ = [
    "LocalOnlyError",
    "Settings",
    "assert_local_only",
    "get_settings",
]
