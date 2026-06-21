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


def _create_reflex_table(db_path: str) -> None:
    """Create the columns of ``reflex_rules`` that the hook reads and writes."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE reflex_rules (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                rule_name TEXT NOT NULL,
                trigger_tool TEXT NOT NULL,
                trigger_pattern TEXT,
                precondition_kind TEXT NOT NULL,
                precondition_param_json TEXT,
                enforcement TEXT NOT NULL DEFAULT 'advisory',
                derived_from_insight_id TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                block_count INTEGER NOT NULL DEFAULT 0,
                advisory_count INTEGER NOT NULL DEFAULT 0,
                last_fired_at TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _seed_reflex(
    db_path: str,
    *,
    rule_name: str,
    trigger_tool: str,
    trigger_pattern: str,
    precondition_kind: str,
    enforcement: str,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO reflex_rules
                (id, workspace_id, rule_name, trigger_tool, trigger_pattern,
                 precondition_kind, precondition_param_json, enforcement,
                 active, created_at, updated_at)
            VALUES (?, 'test-ws', ?, ?, ?, ?, '{}', ?, 1,
                    '2026-05-15T00:00:00Z', '2026-05-15T00:00:00Z')
            """,
            (
                f"rfx_{rule_name}",
                rule_name,
                trigger_tool,
                trigger_pattern,
                precondition_kind,
                enforcement,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _reflex_counts(db_path: str, rule_name: str) -> tuple[int, int]:
    """Return (block_count, advisory_count) for a seeded reflex rule."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT block_count, advisory_count FROM reflex_rules WHERE rule_name = ?",
            (rule_name,),
        ).fetchone()
    finally:
        conn.close()
    return (int(row[0]), int(row[1]))


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


def test_loop_breaker_allows_retry_after_one_block(
    fake_workspace: dict[str, str], tmp_path: Path
) -> None:
    """A mechanical rule blocks the FIRST attempt (exit 2), but the SAME
    (tool, target) retried within the window is allowed (exit 0) -- so a hard
    rule can never deadlock the agent when it cannot satisfy it (e.g. the memory
    MCP server is down and memory_impact_check is unreachable)."""
    _seed_rule(
        fake_workspace["db_path"],
        rule_id="beh_rbe",
        name="read-before-edit",
        applies_to=["enforcement:mechanical", "mechanical:read-before-edit"],
    )
    event = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/strategy/x.py", "new_string": "ok = 1"},
        "cwd": fake_workspace["project_root"],
    }
    env = {
        "MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"],
        "MEMORY_PRETOOLUSE_LOOPBREAK_FILE": str(tmp_path / "lb.json"),
    }
    first = _run(event, env)
    assert first.returncode == 2  # blocked: no prior memory_impact_check (the nudge)
    second = _run(event, env)
    assert second.returncode == 0  # retry allowed -- loop broken, no deadlock


def test_loop_breaker_is_keyed_per_target(
    fake_workspace: dict[str, str], tmp_path: Path
) -> None:
    """Blocking file A does not pre-allow a never-seen file B: the breaker is
    keyed by (tool, target), so every distinct target still gets its one nudge."""
    _seed_rule(
        fake_workspace["db_path"],
        rule_id="beh_rbe",
        name="read-before-edit",
        applies_to=["enforcement:mechanical", "mechanical:read-before-edit"],
    )
    env = {
        "MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"],
        "MEMORY_PRETOOLUSE_LOOPBREAK_FILE": str(tmp_path / "lb.json"),
    }
    a = _run(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/strategy/a.py", "new_string": "ok = 1"},
            "cwd": fake_workspace["project_root"],
        },
        env,
    )
    assert a.returncode == 2
    b = _run(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/strategy/b.py", "new_string": "ok = 1"},
            "cwd": fake_workspace["project_root"],
        },
        env,
    )
    assert b.returncode == 2  # distinct target -> still blocked on its first try


# ----------------------------------------------------------------------------
# Reflex layer wired into the live hook. Before this, reflex_rules were
# evaluated only inside lint()/memory_lint, never on a real tool call, so a
# 'block' reflex silently did nothing and fire counts sat at 0 forever.
# ----------------------------------------------------------------------------

_REFLEX_ENV = {"MEMORY_REFLEX_ENABLED": "1"}


def test_block_reflex_blocks_deploy_without_playbook(
    fake_workspace: dict[str, str], tmp_path: Path
) -> None:
    """A 'block' reflex now enforces on the live path: a deploy Bash command with
    no prior memory_invoke_skill is blocked (exit 2) and the fire is recorded."""
    _create_reflex_table(fake_workspace["db_path"])
    _seed_reflex(
        fake_workspace["db_path"],
        rule_name="deploy-requires-playbook",
        trigger_tool="Bash",
        trigger_pattern="deploy|publish|release",
        precondition_kind="playbook_fetch",
        enforcement="block",
    )
    result = _run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "npm run deploy -- --production"},
            "cwd": fake_workspace["project_root"],
        },
        env_overrides={
            "MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"],
            # Per-test marker so the machine-wide default file can never make this
            # block flip to an allow on a re-run within the loop-break TTL.
            "MEMORY_PRETOOLUSE_LOOPBREAK_FILE": str(tmp_path / "lb.json"),
            **_REFLEX_ENV,
        },
    )
    assert result.returncode == 2
    assert "[reflex]" in result.stderr
    assert "deploy-requires-playbook" in result.stderr
    assert "memory_invoke_skill" in result.stderr  # the advisory names the fix
    assert _reflex_counts(fake_workspace["db_path"], "deploy-requires-playbook")[0] == 1


def test_advisory_reflex_records_fire_without_blocking(
    fake_workspace: dict[str, str], tmp_path: Path
) -> None:
    """An 'advisory' reflex must NOT block (exit 0) but its fire is recorded, so
    advisory_count finally reflects reality instead of staying frozen at 0."""
    _create_reflex_table(fake_workspace["db_path"])
    _seed_reflex(
        fake_workspace["db_path"],
        rule_name="deploy-requires-playbook",
        trigger_tool="Bash",
        trigger_pattern="deploy|publish|release",
        precondition_kind="playbook_fetch",
        enforcement="advisory",
    )
    result = _run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "make publish"},
            "cwd": fake_workspace["project_root"],
        },
        env_overrides={
            "MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"],
            "MEMORY_PRETOOLUSE_LOOPBREAK_FILE": str(tmp_path / "lb.json"),
            **_REFLEX_ENV,
        },
    )
    assert result.returncode == 0  # advisory never blocks
    block_count, advisory_count = _reflex_counts(
        fake_workspace["db_path"], "deploy-requires-playbook"
    )
    assert block_count == 0
    assert advisory_count == 1  # recorded on the live path


def test_block_reflex_allows_when_precondition_met(
    fake_workspace: dict[str, str], tmp_path: Path
) -> None:
    """When the trail already contains the satisfying tool (memory_invoke_skill
    via a transcript), the block reflex does not fire -> exit 0."""
    _create_reflex_table(fake_workspace["db_path"])
    _seed_reflex(
        fake_workspace["db_path"],
        rule_name="deploy-requires-playbook",
        trigger_tool="Bash",
        trigger_pattern="deploy|publish|release",
        precondition_kind="playbook_fetch",
        enforcement="block",
    )
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "memory_invoke_skill"}],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = _run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "npm run release"},
            "cwd": fake_workspace["project_root"],
            "transcript_path": str(transcript),
        },
        env_overrides={
            "MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"],
            "MEMORY_PRETOOLUSE_LOOPBREAK_FILE": str(tmp_path / "lb.json"),
            **_REFLEX_ENV,
        },
    )
    assert result.returncode == 0  # precondition satisfied -> reflex stays quiet


def test_loop_breaker_covers_block_reflex_playbook(
    fake_workspace: dict[str, str], tmp_path: Path
) -> None:
    """A 'block' reflex whose prerequisite is memory_invoke_skill must also be
    loop-broken: blocked once (the nudge), then the same retry is allowed so an
    unreachable MCP server cannot deadlock the agent."""
    _create_reflex_table(fake_workspace["db_path"])
    _seed_reflex(
        fake_workspace["db_path"],
        rule_name="deploy-requires-playbook",
        trigger_tool="Bash",
        trigger_pattern="deploy|publish|release",
        precondition_kind="playbook_fetch",
        enforcement="block",
    )
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "npm run deploy"},
        "cwd": fake_workspace["project_root"],
    }
    env = {
        "MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"],
        "MEMORY_PRETOOLUSE_LOOPBREAK_FILE": str(tmp_path / "lb.json"),
        **_REFLEX_ENV,
    }
    first = _run(event, env)
    assert first.returncode == 2  # the nudge
    second = _run(event, env)
    assert second.returncode == 0  # loop broken -- no deadlock


def test_loop_breaker_keyed_per_bash_command(
    fake_workspace: dict[str, str], tmp_path: Path
) -> None:
    """Blocking ONE deploy command must not pre-allow a DIFFERENT deploy command
    within the TTL. Without folding the command into the loop-break key, every
    Bash call collapses to one key and the first block free-passes all others --
    so this guards the per-command discrimination for command-shaped tools."""
    _create_reflex_table(fake_workspace["db_path"])
    _seed_reflex(
        fake_workspace["db_path"],
        rule_name="deploy-requires-playbook",
        trigger_tool="Bash",
        trigger_pattern="deploy|publish|release",
        precondition_kind="playbook_fetch",
        enforcement="block",
    )
    env = {
        "MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"],
        "MEMORY_PRETOOLUSE_LOOPBREAK_FILE": str(tmp_path / "lb.json"),
        **_REFLEX_ENV,
    }
    first = _run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "npm run deploy"},
            "cwd": fake_workspace["project_root"],
        },
        env,
    )
    assert first.returncode == 2  # first deploy blocked (its nudge)
    other = _run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "make release"},
            "cwd": fake_workspace["project_root"],
        },
        env,
    )
    assert other.returncode == 2  # a DIFFERENT deploy command still blocks


def test_loop_broken_retry_does_not_overcount_block_count(
    fake_workspace: dict[str, str], tmp_path: Path
) -> None:
    """A same-command retry that the loop-breaker ALLOWS must not bump block_count
    -- block_count counts calls actually blocked, not loop-broken retries."""
    _create_reflex_table(fake_workspace["db_path"])
    _seed_reflex(
        fake_workspace["db_path"],
        rule_name="deploy-requires-playbook",
        trigger_tool="Bash",
        trigger_pattern="deploy|publish|release",
        precondition_kind="playbook_fetch",
        enforcement="block",
    )
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "npm run deploy"},
        "cwd": fake_workspace["project_root"],
    }
    env = {
        "MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"],
        "MEMORY_PRETOOLUSE_LOOPBREAK_FILE": str(tmp_path / "lb.json"),
        **_REFLEX_ENV,
    }
    assert _run(event, env).returncode == 2  # blocked (counted)
    assert _run(event, env).returncode == 0  # loop broken -> allowed (NOT counted)
    assert _reflex_counts(fake_workspace["db_path"], "deploy-requires-playbook")[0] == 1


def test_co_located_block_reflexes_each_get_own_nudge(
    fake_workspace: dict[str, str], tmp_path: Path
) -> None:
    """Two BLOCK reflexes on the SAME Bash target each get their own one-shot
    nudge: satisfying one rule's prerequisite must not consume the other's nudge.
    The loop-break key is prerequisite-scoped, so the still-unsatisfied rule keeps
    blocking instead of being silently bypassed (pins the round-2 fix)."""
    _create_reflex_table(fake_workspace["db_path"])
    _seed_reflex(
        fake_workspace["db_path"],
        rule_name="deploy-requires-playbook",
        trigger_tool="Bash",
        trigger_pattern="deploy|publish|release",
        precondition_kind="playbook_fetch",
        enforcement="block",
    )
    _seed_reflex(
        fake_workspace["db_path"],
        rule_name="deploy-requires-impact",
        trigger_tool="Bash",
        trigger_pattern="deploy|publish|release",
        precondition_kind="impact_check_within_seconds",
        enforcement="block",
    )
    env = {
        "MEMORY_WORKSPACES_FILE": fake_workspace["registry_path"],
        "MEMORY_PRETOOLUSE_LOOPBREAK_FILE": str(tmp_path / "lb.json"),
        **_REFLEX_ENV,
    }
    target = {
        "tool_name": "Bash",
        "tool_input": {"command": "npm run deploy"},
        "cwd": fake_workspace["project_root"],
    }
    # Call 1: empty trail -> BOTH reflexes block.
    assert _run(target, env).returncode == 2
    # Call 2: trail satisfies the playbook prerequisite (memory_invoke_skill) but
    # NOT the impact-check one. The impact-check reflex carries a different
    # prerequisite token, so it must STILL block -- not ride the playbook rule's
    # earlier nudge.
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "memory_invoke_skill"}],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    second = _run({**target, "transcript_path": str(transcript)}, env)
    assert second.returncode == 2  # co-located impact-check reflex is NOT bypassed
    assert "memory_impact_check" in second.stderr
