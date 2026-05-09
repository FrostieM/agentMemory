"""Tests for workspace_schema() default resolution (Phase 2.7).

Prior to Phase 2.7 of v2.2 consolidation, ``workspace_schema()`` returned
``{"default": settings.workspace_id}`` directly. In hub-mode chats with
no ``MEMORY_WORKSPACE_ID`` env, that gave operators ``default: "default"``
in every tool schema while the runtime was actually routing to a real
registered workspace.

The fix prefers (in order):
  1. ``settings.workspace_id`` when explicitly set (≠ "default" sentinel).
  2. Registry's first entry id when the placeholder is in effect.
  3. ``"default"`` only when the registry is also empty.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.mcp import stdio_runtime


def _settings_with_registry(tmp_path: Path, workspace_id: str, entries: list[str]) -> Settings:
    """Build a Settings pointing at a temp workspaces.json with named entries.

    pydantic-settings uses ``validation_alias`` on the relevant fields
    (``MEMORY_WORKSPACE_ID``, ``MEMORY_WORKSPACES_FILE``), so kwargs must
    use the alias names — passing the python field name silently falls
    back to the env-resolved default path. Using aliases keeps tests
    isolated from the operator's real ~/.agent_memory/workspaces.json.
    """
    registry_path = tmp_path / "workspaces.json"
    payload = {
        "version": 1,
        "workspaces": [
            {
                "id": entry_id,
                "db_path": str(tmp_path / f"{entry_id}.db"),
                "vector_path": str(tmp_path / f"{entry_id}.lance"),
                "label": entry_id,
                "project_root": "",
                "registered_at": "",
                "last_seen_at": "",
                "extra": {},
            }
            for entry_id in entries
        ],
    }
    registry_path.write_text(json.dumps(payload), encoding="utf-8")
    return Settings(
        MEMORY_WORKSPACE_ID=workspace_id,  # type: ignore[call-arg]
        MEMORY_WORKSPACES_FILE=registry_path,  # type: ignore[call-arg]
    )


def test_workspace_schema_uses_explicit_settings_workspace_id(tmp_path: Path) -> None:
    """When MEMORY_WORKSPACE_ID is set, the schema reflects that exact value
    even when the registry could offer alternatives — explicit beats implicit."""
    settings = _settings_with_registry(
        tmp_path, workspace_id="agentLight", entries=["copyBot", "agentLight", "demo"]
    )
    with patch.object(stdio_runtime._runtime, "settings", settings):
        schema = stdio_runtime.workspace_schema()
    assert schema == {"type": "string", "default": "agentLight"}


def test_workspace_schema_falls_back_to_first_registry_entry(tmp_path: Path) -> None:
    """When settings.workspace_id is the placeholder 'default', resolve via
    the registry's first entry instead of the misleading 'default' sentinel."""
    settings = _settings_with_registry(
        tmp_path, workspace_id="default", entries=["agentLight", "copyBot"]
    )
    with patch.object(stdio_runtime._runtime, "settings", settings):
        schema = stdio_runtime.workspace_schema()
    assert schema == {"type": "string", "default": "agentLight"}


def test_workspace_schema_keeps_default_when_registry_empty(tmp_path: Path) -> None:
    """No env-set workspace and no registry — fall back to the literal
    'default' so the schema is at least syntactically valid."""
    settings = _settings_with_registry(tmp_path, workspace_id="default", entries=[])
    with patch.object(stdio_runtime._runtime, "settings", settings):
        schema = stdio_runtime.workspace_schema()
    assert schema == {"type": "string", "default": "default"}


def test_workspace_schema_keeps_default_when_registry_load_fails(tmp_path: Path) -> None:
    """If the registry file is malformed or unreachable, the schema must
    not crash the MCP server bootstrap. Falls back to 'default' silently."""
    bad_registry = tmp_path / "broken.json"
    bad_registry.write_text("{ this is not valid json", encoding="utf-8")
    settings = Settings(
        MEMORY_WORKSPACE_ID="default",  # type: ignore[call-arg]
        MEMORY_WORKSPACES_FILE=bad_registry,  # type: ignore[call-arg]
    )
    with patch.object(stdio_runtime._runtime, "settings", settings):
        schema = stdio_runtime.workspace_schema()
    assert schema == {"type": "string", "default": "default"}
