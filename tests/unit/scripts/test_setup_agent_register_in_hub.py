"""Verify `setup_agent.py:register_workspace_in_hub` writes a correct
registry entry for project mode.

The function is what makes `setup_agent.py --project` discoverable by
the hub UI and the `/memory/workspaces` endpoint. It writes directly to
the registry file (no HTTP), so this is testable in-process.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_setup() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "setup_agent_under_test", repo_root / "scripts" / "setup_agent.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_register_workspace_in_hub_creates_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = _load_setup()
    project_root = tmp_path / "alpha"
    project_root.mkdir()
    db_path = project_root / ".agent_memory" / "memory.db"
    vec_path = project_root / ".agent_memory" / "vectors.lance"
    registry_path = tmp_path / "workspaces.json"
    monkeypatch.setenv("MEMORY_WORKSPACES_FILE", str(registry_path))

    setup.register_workspace_in_hub(
        workspace_id="alpha",
        db_path=db_path,
        vector_path=vec_path,
        project_root=project_root,
    )

    from agent_memory_lite.config.workspace_registry import WorkspaceRegistry  # noqa: PLC0415

    entries = WorkspaceRegistry(registry_path).list()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.id == "alpha"
    assert entry.db_path == str(db_path)
    assert entry.vector_path == str(vec_path)
    assert entry.project_root == str(project_root)
    assert entry.label == "alpha"


def test_claude_mcp_entry_project_mode_full_env(tmp_path: Path) -> None:
    """`setup_agent.py --project` bakes the full strict-isolation env."""
    setup = _load_setup()
    entry = setup.claude_mcp_entry(
        Path("/usr/bin/python3"),
        project_root=tmp_path,
        workspace_id="myproj",
    )
    env = entry["env"]
    assert env["MEMORY_WORKSPACE_ID"] == "myproj"
    assert env["MEMORY_FORBID_DEFAULT_WORKSPACE"] == "true"
    assert env["MEMORY_STRICT_WORKSPACE_ISOLATION"] == "true"
    assert env["MEMORY_DB_PATH"] == str(tmp_path / ".agent_memory" / "memory.db")
    assert env["VECTOR_DB_PATH"] == str(tmp_path / ".agent_memory" / "vectors.lance")
    assert entry["args"] == ["-m", "agent_memory_lite.mcp.stdio_server"]


def test_claude_mcp_entry_global_mode_no_strict(tmp_path: Path) -> None:
    """Global-only setup must not set strict isolation."""
    setup = _load_setup()
    entry = setup.claude_mcp_entry(Path("/usr/bin/python3"))
    env = entry["env"]
    assert "MEMORY_STRICT_WORKSPACE_ISOLATION" not in env
    assert "MEMORY_DB_PATH" not in env
    assert "MEMORY_WORKSPACE_ID" not in env
