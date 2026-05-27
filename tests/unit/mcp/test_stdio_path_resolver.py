from __future__ import annotations

import json
from pathlib import Path

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.mcp.stdio_path_resolver import resolve_paths_from_cwd


def _settings(registry: Path) -> Settings:
    return Settings(
        MEMORY_WORKSPACES_FILE=registry,
        MEMORY_WORKSPACE_ID="default",
        MEMORY_DB_PATH=Path("anchor/memory.db"),
        VECTOR_DB_PATH=Path("anchor/vectors.lance"),
        MEMORY_STRICT_WORKSPACE_ISOLATION=False,
        MEMORY_FORBID_DEFAULT_WORKSPACE=False,
        MEMORY_HUB_MODE=False,
    )


def test_project_cwd_uses_registry_workspace_id_and_strict_isolation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "copyBot"
    memory_dir = project / ".agent_memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "memory.db").write_text("", encoding="utf-8")
    (memory_dir / "vectors.lance").mkdir()
    other = tmp_path / "other"
    other.mkdir()
    registry = tmp_path / "workspaces.json"
    registry.write_text(
        json.dumps(
            {
                "workspaces": [
                    {
                        "id": "copyBot",
                        "db_path": str(memory_dir / "memory.db"),
                        "vector_path": str(memory_dir / "vectors.lance"),
                        "project_root": str(project),
                    },
                    {
                        "id": "agent-memory-lite",
                        "db_path": str(other / "memory.db"),
                        "vector_path": str(other / "vectors.lance"),
                        "project_root": str(other),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("MEMORY_DB_PATH", raising=False)
    monkeypatch.chdir(project)

    resolved = resolve_paths_from_cwd(_settings(registry))

    assert resolved.db_path == memory_dir / "memory.db"
    assert resolved.vector_db_path == memory_dir / "vectors.lance"
    assert resolved.workspace_id == "copyBot"
    assert resolved.strict_workspace_isolation is True
    assert resolved.forbid_default_workspace is True
    assert resolved.hub_mode is False


def test_unregistered_local_memory_keeps_existing_workspace_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "unregistered"
    memory_dir = project / ".agent_memory"
    memory_dir.mkdir(parents=True)
    registry = tmp_path / "workspaces.json"
    registry.write_text(json.dumps({"workspaces": []}), encoding="utf-8")
    monkeypatch.delenv("MEMORY_DB_PATH", raising=False)
    monkeypatch.chdir(project)

    resolved = resolve_paths_from_cwd(_settings(registry))

    assert resolved.db_path == memory_dir / "memory.db"
    assert resolved.workspace_id == "default"


def test_project_subdir_uses_registered_parent_workspace(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "copyBot"
    subdir = project / "src"
    memory_dir = project / ".agent_memory"
    subdir.mkdir(parents=True)
    memory_dir.mkdir()
    other = tmp_path / "agent-memory-lite"
    other.mkdir()
    registry = tmp_path / "workspaces.json"
    registry.write_text(
        json.dumps(
            {
                "workspaces": [
                    {
                        "id": "agent-memory-lite",
                        "db_path": str(other / ".agent_memory" / "memory.db"),
                        "vector_path": str(other / ".agent_memory" / "vectors.lance"),
                        "project_root": str(other),
                    },
                    {
                        "id": "copyBot",
                        "db_path": str(memory_dir / "memory.db"),
                        "vector_path": str(memory_dir / "vectors.lance"),
                        "project_root": str(project),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("MEMORY_DB_PATH", raising=False)
    monkeypatch.chdir(subdir)

    resolved = resolve_paths_from_cwd(_settings(registry))

    assert resolved.workspace_id == "copyBot"
    assert resolved.db_path == memory_dir / "memory.db"
    assert resolved.strict_workspace_isolation is True
    assert resolved.hub_mode is False
