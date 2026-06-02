from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.mcp.stdio_path_resolver import (
    assert_anchor_consistent,
    resolve_paths_from_cwd,
)


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


# ---------------------------------------------------------------------------
# Startup anchor-consistency assertion (Phase 3 / P4): refuse to serve when a
# registered anchor workspace_id is pointed at another workspace's DB -- the
# "server stuck on the wrong anchor" class from the 2026-05 incident.
# ---------------------------------------------------------------------------


def _settings_with(
    registry: Path, *, workspace_id: str, db_path: Path, forbid_default: bool = False
) -> Settings:
    return Settings(
        MEMORY_WORKSPACES_FILE=registry,
        MEMORY_WORKSPACE_ID=workspace_id,
        MEMORY_DB_PATH=db_path,
        VECTOR_DB_PATH=db_path.with_suffix(".lance"),
        MEMORY_STRICT_WORKSPACE_ISOLATION=True,
        MEMORY_FORBID_DEFAULT_WORKSPACE=forbid_default,
        MEMORY_HUB_MODE=False,
    )


def _write_single_registry(registry: Path, *, ws_id: str, project: Path) -> None:
    registry.write_text(
        json.dumps(
            {
                "workspaces": [
                    {
                        "id": ws_id,
                        "db_path": str(project / "memory.db"),
                        "vector_path": str(project / "vectors.lance"),
                        "project_root": str(project),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_assert_anchor_consistent_raises_on_mismatched_db(tmp_path: Path) -> None:
    """A registered anchor id pointed at another DB (mis-anchored MEMORY_DB_PATH /
    inherited .mcp.json env) must fail closed at startup."""
    project = tmp_path / "copyBot"
    project.mkdir()
    registry = tmp_path / "workspaces.json"
    _write_single_registry(registry, ws_id="copyBot", project=project)
    settings = _settings_with(
        registry, workspace_id="copyBot", db_path=tmp_path / "WRONG" / "memory.db"
    )
    with pytest.raises(ValueError, match=r"mis-anchored|mis-configured"):
        assert_anchor_consistent(settings)


def test_assert_anchor_consistent_passes_when_db_matches(tmp_path: Path) -> None:
    """The anchor id writing to its own registered DB is fine."""
    project = tmp_path / "copyBot"
    project.mkdir()
    registry = tmp_path / "workspaces.json"
    _write_single_registry(registry, ws_id="copyBot", project=project)
    settings = _settings_with(registry, workspace_id="copyBot", db_path=project / "memory.db")
    assert_anchor_consistent(settings)  # must not raise


def test_assert_anchor_consistent_skips_unregistered_anchor(tmp_path: Path) -> None:
    """An unregistered anchor on an unregistered DB has nothing authoritative to
    contradict -- skip (a fresh / local-only workspace)."""
    registry = tmp_path / "workspaces.json"
    registry.write_text(json.dumps({"workspaces": []}), encoding="utf-8")
    settings = _settings_with(registry, workspace_id="default", db_path=tmp_path / "anything.db")
    assert_anchor_consistent(settings)  # must not raise


def test_assert_anchor_consistent_raises_when_unregistered_anchor_on_foreign_db(
    tmp_path: Path,
) -> None:
    """An UNREGISTERED anchor id sitting on another workspace's registered DB
    (e.g. MEMORY_WORKSPACE_ID=scratch pinned to copyBot's DB) must fail closed --
    not slip through to be caught late by the per-DB manifest guard."""
    project = tmp_path / "copyBot"
    project.mkdir()
    registry = tmp_path / "workspaces.json"
    _write_single_registry(registry, ws_id="copyBot", project=project)
    settings = _settings_with(registry, workspace_id="scratch", db_path=project / "memory.db")
    with pytest.raises(ValueError, match="copyBot"):
        assert_anchor_consistent(settings)


def test_assert_anchor_consistent_refuses_forbidden_default_anchor(tmp_path: Path) -> None:
    """A bare/unregistered context that resolves to 'default' while
    forbid_default is set must fail closed AT STARTUP with a clear message,
    not opaquely on the first tool call (final-audit M1)."""
    registry = tmp_path / "workspaces.json"
    registry.write_text(json.dumps({"workspaces": []}), encoding="utf-8")
    settings = _settings_with(
        registry,
        workspace_id="default",
        db_path=tmp_path / "anything.db",
        forbid_default=True,
    )
    with pytest.raises(ValueError, match=r"forbidden default|FORBID_DEFAULT"):
        assert_anchor_consistent(settings)
