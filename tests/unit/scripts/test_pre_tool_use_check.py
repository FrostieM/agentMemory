"""Integration-style tests for the PreToolUse hook entry script.

We invoke the script as a subprocess and feed it the same JSON shape
Claude Code does. The DB and registry are temporary fixtures so the
test is hermetic; no real workspace is touched.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "pre_tool_use_check.py"


@pytest.fixture
def fake_workspace(tmp_path: Path) -> Iterator[dict[str, str]]:
    """Create a fake project + DB + registry pointing at it."""
    project_root = tmp_path / "proj"
    project_root.mkdir()
    db_path = tmp_path / "memory.db"
    registry_path = tmp_path / "workspaces.json"

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE behavior_instructions (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                name TEXT NOT NULL,
                rule TEXT NOT NULL,
                applies_to_json TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                pinned INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    registry_path.write_text(
        json.dumps(
            {
                "workspaces": [
                    {
                        "id": "test-ws",
                        "db_path": str(db_path),
                        "vector_path": str(tmp_path / "v.lance"),
                        "project_root": str(project_root),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return {
        "project_root": str(project_root),
        "db_path": str(db_path),
        "registry_path": str(registry_path),
    }


def _seed_rule(
    db_path: str,
    *,
    rule_id: str,
    name: str,
    applies_to: list[str],
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO behavior_instructions (
                id, workspace_id, name, rule, applies_to_json, active, pinned, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule_id,
                "test-ws",
                name,
                "rule body",
                json.dumps(applies_to),
                1,
                0,
                "2026-05-15T00:00:00Z",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _run(event: dict, env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    # Make sure the import path includes the repo so the lazy import
    # of enforcement.dispatch works in the subprocess.
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )


def test_no_workspace_match_returns_zero(fake_workspace: dict[str, str], tmp_path: Path) -> None:
    """When cwd is outside any registered project, exit 0 (allow)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    result = _run(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": "x", "new_string": "y"},
            "cwd": str(outside),
        },
        env_overrides={"MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"]},
    )
    assert result.returncode == 0


def test_bypass_env_flag_skips_all_checks(
    fake_workspace: dict[str, str],
) -> None:
    _seed_rule(
        fake_workspace["db_path"],
        rule_id="beh_mn",
        name="magic-number",
        applies_to=["enforcement:mechanical", "mechanical:no-magic-number"],
    )
    result = _run(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "src/strategy/x.py",
                "new_string": "if confidence > 0.85:\n    pass",
            },
            "cwd": fake_workspace["project_root"],
        },
        env_overrides={
            "MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"],
            "MEMORY_SKIP_PRETOOLUSE_CHECK": "1",
        },
    )
    assert result.returncode == 0


def test_magic_number_rule_blocks_edit_with_exit_2(
    fake_workspace: dict[str, str],
) -> None:
    _seed_rule(
        fake_workspace["db_path"],
        rule_id="beh_mn",
        name="magic-number",
        applies_to=["enforcement:mechanical", "mechanical:no-magic-number"],
    )
    result = _run(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "src/strategy/x.py",
                "new_string": "if confidence > 0.85:\n    pass",
            },
            "cwd": fake_workspace["project_root"],
        },
        env_overrides={"MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"]},
    )
    assert result.returncode == 2
    assert "beh_mn" in result.stderr
    assert "0.85" in result.stderr


def test_empty_stdin_returns_zero(fake_workspace: dict[str, str]) -> None:
    del fake_workspace
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="",
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0


def test_malformed_stdin_returns_zero(fake_workspace: dict[str, str]) -> None:
    del fake_workspace
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="{not json",
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0


def test_no_active_rules_allows_call(
    fake_workspace: dict[str, str],
) -> None:
    """Empty DB → no rules → exit 0 even on tool calls that would otherwise fire."""
    result = _run(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "src/strategy/x.py",
                "new_string": "if confidence > 0.85:\n    pass",
            },
            "cwd": fake_workspace["project_root"],
        },
        env_overrides={"MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"]},
    )
    assert result.returncode == 0


def test_explicit_workspace_env_override(
    fake_workspace: dict[str, str],
) -> None:
    _seed_rule(
        fake_workspace["db_path"],
        rule_id="beh_mn",
        name="magic-number",
        applies_to=["enforcement:mechanical", "mechanical:no-magic-number"],
    )
    # cwd unrelated, but AGENT_MEMORY_WORKSPACE points to test-ws.
    result = _run(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "src/strategy/x.py",
                "new_string": "if confidence > 0.85:\n    pass",
            },
            "cwd": "/totally/unrelated/path",
        },
        env_overrides={
            "MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"],
            "AGENT_MEMORY_WORKSPACE": "test-ws",
        },
    )
    assert result.returncode == 2
