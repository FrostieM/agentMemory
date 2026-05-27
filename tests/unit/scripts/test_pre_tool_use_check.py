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
            CREATE TABLE behaviors (
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
    pinned: int = 1,
) -> None:
    """Seed a behavior. Default pinned=1 since loader only enforces pinned rules."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO behaviors (
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
                pinned,
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


def test_debug_log_records_allow_invocation(fake_workspace: dict[str, str], tmp_path: Path) -> None:
    """When MEMORY_PRETOOLUSE_DEBUG points at a file, every hook invocation appends a row."""
    log_path = tmp_path / "trace.log"
    result = _run(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/api/x.py", "new_string": "ok = 1"},
            "cwd": fake_workspace["project_root"],
        },
        env_overrides={
            "MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"],
            "MEMORY_PRETOOLUSE_DEBUG": str(log_path),
        },
    )
    assert result.returncode == 0
    assert log_path.exists()
    rows = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1
    fields = rows[0].split("\t")
    assert fields[1] == "Edit"
    assert fields[2] == "test-ws"
    assert fields[3] == "allow"


def test_debug_log_records_block_invocation(fake_workspace: dict[str, str], tmp_path: Path) -> None:
    _seed_rule(
        fake_workspace["db_path"],
        rule_id="beh_mn",
        name="magic-number",
        applies_to=["enforcement:mechanical", "mechanical:no-magic-number"],
    )
    log_path = tmp_path / "trace.log"
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
            "MEMORY_PRETOOLUSE_DEBUG": str(log_path),
        },
    )
    assert result.returncode == 2
    rows = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1
    fields = rows[0].split("\t")
    assert fields[3] == "block"


def test_debug_log_silent_when_env_unset(fake_workspace: dict[str, str], tmp_path: Path) -> None:
    log_path = tmp_path / "trace.log"
    _run(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/a.py", "new_string": "x"},
            "cwd": fake_workspace["project_root"],
        },
        env_overrides={"MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"]},
    )
    assert not log_path.exists()


def test_debug_log_records_bypass(tmp_path: Path) -> None:
    log_path = tmp_path / "trace.log"
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"tool_name": "Edit", "tool_input": {}}),
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "MEMORY_SKIP_PRETOOLUSE_CHECK": "1",
            "MEMORY_PRETOOLUSE_DEBUG": str(log_path),
        },
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    rows = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1
    assert rows[0].endswith("bypass")


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


def test_canonical_workspace_env_override(
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
            "cwd": "/totally/unrelated/path",
        },
        env_overrides={
            "MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"],
            "MEMORY_WORKSPACE_ID": "test-ws",
            "AGENT_MEMORY_WORKSPACE": "",
        },
    )
    assert result.returncode == 2


def test_hook_never_crashes_on_corrupt_db(
    fake_workspace: dict[str, str],
    tmp_path: Path,
) -> None:
    """v3.0.0-final invariant: the PreToolUse hook MUST exit 0 even on
    unexpected errors. A traceback to stderr can be misinterpreted by
    Claude Code as a tool-call block, breaking the user's workflow.

    Test: point the hook at a registry entry whose db_path is a real
    file but NOT a valid SQLite database (random bytes). The lazy
    import of enforcement.dispatch succeeds, but the actual decide()
    call hits a non-sqlite3.Error (DatabaseError → sqlite3.Error, OK;
    but corrupt schema may surface other errors). The hook must
    fail-open and exit 0.
    """
    bogus_db = tmp_path / "bogus.db"
    bogus_db.write_bytes(b"NOT A SQLITE DATABASE \x00\x01\x02\x03" * 10)
    bogus_registry = tmp_path / "bogus_workspaces.json"
    bogus_registry.write_text(
        json.dumps(
            {
                "workspaces": [
                    {
                        "id": "bogus-ws",
                        "db_path": str(bogus_db),
                        "vector_path": str(tmp_path / "noop.lance"),
                        "project_root": fake_workspace["project_root"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = _run(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": "x.py", "new_string": "y"},
            "cwd": fake_workspace["project_root"],
        },
        env_overrides={
            "MEMORY_WORKSPACES_FILE": str(bogus_registry),
            "AGENT_MEMORY_WORKSPACE": "bogus-ws",
        },
    )
    # Hook must allow (exit 0). Anything else suggests a traceback escaped.
    assert result.returncode == 0, (
        f"hook crashed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
