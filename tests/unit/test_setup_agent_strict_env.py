"""Unit tests for `scripts/setup_agent.py:claude_mcp_entry` env baking.

The contract: when called with both `project_root` and a non-default
`workspace_id`, the returned MCP entry must include
`MEMORY_STRICT_WORKSPACE_ISOLATION=true` so the project MCP refuses to
read or write any other workspace by default.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_setup_agent() -> ModuleType:
    """Import scripts/setup_agent.py without triggering its CLI."""
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "setup_agent_under_test", repo_root / "scripts" / "setup_agent.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_project_mode_bakes_strict_isolation(tmp_path: Path) -> None:
    setup = _load_setup_agent()
    entry = setup.claude_mcp_entry(
        Path("/usr/bin/python3"),
        project_root=tmp_path,
        workspace_id="alpha",
    )
    env = entry["env"]
    assert env["MEMORY_WORKSPACE_ID"] == "alpha"
    assert env["MEMORY_FORBID_DEFAULT_WORKSPACE"] == "true"
    assert env["MEMORY_STRICT_WORKSPACE_ISOLATION"] == "true"
    assert env["MEMORY_DB_PATH"].endswith("memory.db")
    assert env["VECTOR_DB_PATH"].endswith("vectors.lance")


def test_global_mode_does_not_set_strict(tmp_path: Path) -> None:
    setup = _load_setup_agent()
    entry = setup.claude_mcp_entry(Path("/usr/bin/python3"))
    env = entry["env"]
    assert "MEMORY_STRICT_WORKSPACE_ISOLATION" not in env
    assert "MEMORY_DB_PATH" not in env
    assert "MEMORY_WORKSPACE_ID" not in env


def test_default_workspace_in_project_mode_skips_strict(tmp_path: Path) -> None:
    """workspace_id='default' is the global fallback, never strict-isolated."""
    setup = _load_setup_agent()
    entry = setup.claude_mcp_entry(
        Path("/usr/bin/python3"),
        project_root=tmp_path,
        workspace_id="default",
    )
    env = entry["env"]
    assert env.get("MEMORY_STRICT_WORKSPACE_ISOLATION") is None
    assert env.get("MEMORY_FORBID_DEFAULT_WORKSPACE") is None
