"""Unit tests for the hub-mode workspace registry."""

from __future__ import annotations

import json
from pathlib import Path

from agent_memory_lite.config.workspace_registry import WorkspaceRegistry


def test_register_creates_entry(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path / "workspaces.json")
    entry = registry.register(
        workspace_id="agentLight",
        db_path="/projects/agent-memory-lite/.agent_memory/memory.db",
        vector_path="/projects/agent-memory-lite/.agent_memory/vectors.lance",
        label="agent-memory-lite",
        project_root="/projects/agent-memory-lite",
    )
    assert entry.id == "agentLight"
    assert entry.db_path.endswith("memory.db")
    payload = json.loads(registry.path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["workspaces"][0]["id"] == "agentLight"


def test_register_updates_existing_entry(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path / "workspaces.json")
    registry.register(
        workspace_id="agentLight",
        db_path="/old/memory.db",
        vector_path="/old/vectors.lance",
    )
    entry = registry.register(
        workspace_id="agentLight",
        db_path="/new/memory.db",
        vector_path="/new/vectors.lance",
        label="renamed",
    )
    assert entry.db_path == "/new/memory.db"
    assert entry.label == "renamed"
    assert len(registry.list()) == 1


def test_remove_returns_true_when_present(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path / "workspaces.json")
    registry.register(
        workspace_id="agentLight",
        db_path="/projects/.agent_memory/memory.db",
        vector_path="/projects/.agent_memory/vectors.lance",
    )
    assert registry.remove("agentLight") is True
    assert registry.remove("agentLight") is False
    assert registry.list() == []


def test_list_handles_missing_or_corrupted_file(tmp_path: Path) -> None:
    registry = WorkspaceRegistry(tmp_path / "workspaces.json")
    assert registry.list() == []
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text("not json", encoding="utf-8")
    assert registry.list() == []
