"""Phase 4: reflex_check tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.enforcement.reflex_check import (
    check_reflexes,
    has_block_violation,
    record_fired,
)
from agent_memory_lite.utils.time import iso_now

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
REFLEX_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0005_reflexes.sql"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(REFLEX_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_rule(
    conn: sqlite3.Connection,
    *,
    rule_name: str,
    trigger_tool: str,
    precondition_kind: str = "impact_check_within_seconds",
    trigger_pattern: str = "",
    enforcement: str = "block",
) -> None:
    conn.execute(
        """INSERT INTO reflex_rules
           (id, workspace_id, rule_name, trigger_tool, trigger_pattern,
            precondition_kind, precondition_param_json, enforcement,
            active, created_at, updated_at)
           VALUES (?, 'ws', ?, ?, ?, ?, '{}', ?, 1, ?, ?)""",
        (
            f"reflex_{rule_name}",
            rule_name,
            trigger_tool,
            trigger_pattern,
            precondition_kind,
            enforcement,
            iso_now(),
            iso_now(),
        ),
    )
    conn.commit()


# ============================================================
# basic gating
# ============================================================


def test_no_rules_returns_no_violations(conn: sqlite3.Connection) -> None:
    violations = check_reflexes(
        conn, workspace_id="ws", tool_name="Edit", tool_payload={}, trail=[]
    )
    assert violations == []


def test_rule_fires_when_precondition_missing(conn: sqlite3.Connection) -> None:
    _seed_rule(conn, rule_name="edit-needs-impact-check", trigger_tool="Edit")
    violations = check_reflexes(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/x.py"},
        trail=["Read"],  # has Read, but not memory_impact_check
    )
    assert len(violations) == 1
    assert violations[0].rule_name == "edit-needs-impact-check"
    assert violations[0].enforcement == "block"


def test_rule_skipped_when_precondition_satisfied(conn: sqlite3.Connection) -> None:
    _seed_rule(conn, rule_name="edit-needs-impact-check", trigger_tool="Edit")
    violations = check_reflexes(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/x.py"},
        trail=["memory_impact_check", "Edit"],
    )
    assert violations == []


def test_inactive_rule_does_not_fire(conn: sqlite3.Connection) -> None:
    _seed_rule(conn, rule_name="dormant", trigger_tool="Edit")
    conn.execute("UPDATE reflex_rules SET active = 0 WHERE rule_name = 'dormant'")
    conn.commit()
    violations = check_reflexes(
        conn, workspace_id="ws", tool_name="Edit", tool_payload={}, trail=[]
    )
    assert violations == []


# ============================================================
# pattern matching
# ============================================================


def test_trigger_pattern_restricts_scope(conn: sqlite3.Connection) -> None:
    _seed_rule(
        conn,
        rule_name="only-py",
        trigger_tool="Edit",
        trigger_pattern=r"\.py$",
    )
    # .py file -> rule fires.
    v_py = check_reflexes(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/x.py"},
        trail=[],
    )
    assert len(v_py) == 1
    # .md file -> rule does not fire.
    v_md = check_reflexes(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "README.md"},
        trail=[],
    )
    assert v_md == []


def test_invalid_regex_skips_rule(conn: sqlite3.Connection) -> None:
    _seed_rule(
        conn,
        rule_name="bad-regex",
        trigger_tool="Edit",
        trigger_pattern="[invalid(",
    )
    # Bad regex returns False -> rule does not fire (we don't crash).
    violations = check_reflexes(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "x.py"},
        trail=[],
    )
    assert violations == []


# ============================================================
# precondition kinds
# ============================================================


def test_memory_search_precondition(conn: sqlite3.Connection) -> None:
    _seed_rule(
        conn,
        rule_name="search-before-decision",
        trigger_tool="memory_write",
        precondition_kind="memory_search_within_seconds",
    )
    # Without search in trail -> fires.
    v_no = check_reflexes(
        conn, workspace_id="ws", tool_name="memory_write", tool_payload={}, trail=[]
    )
    assert len(v_no) == 1
    # With search in trail -> satisfied.
    v_ok = check_reflexes(
        conn,
        workspace_id="ws",
        tool_name="memory_write",
        tool_payload={},
        trail=["memory_search"],
    )
    assert v_ok == []


def test_playbook_fetch_precondition(conn: sqlite3.Connection) -> None:
    _seed_rule(
        conn,
        rule_name="bash-needs-playbook",
        trigger_tool="Bash",
        precondition_kind="playbook_fetch",
    )
    v_no = check_reflexes(conn, workspace_id="ws", tool_name="Bash", tool_payload={}, trail=[])
    assert len(v_no) == 1
    v_ok = check_reflexes(
        conn,
        workspace_id="ws",
        tool_name="Bash",
        tool_payload={},
        trail=["memory_invoke_skill"],
    )
    assert v_ok == []


def test_unknown_precondition_does_not_fire(conn: sqlite3.Connection) -> None:
    """Defensive: a typo in operator-seeded rule must not lock the agent out."""
    _seed_rule(
        conn,
        rule_name="typo",
        trigger_tool="Edit",
        precondition_kind="not_a_real_kind",
    )
    violations = check_reflexes(
        conn, workspace_id="ws", tool_name="Edit", tool_payload={}, trail=[]
    )
    assert violations == []


# ============================================================
# block override
# ============================================================


def test_block_override_demotes_to_advisory(conn: sqlite3.Connection) -> None:
    _seed_rule(
        conn,
        rule_name="strict",
        trigger_tool="Edit",
        enforcement="block",
    )
    violations = check_reflexes(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={},
        trail=[],
        block_override=True,
    )
    assert len(violations) == 1
    assert violations[0].enforcement == "advisory"
    assert not has_block_violation(violations)


# ============================================================
# pre-migration compatibility
# ============================================================


def test_pre_migration_db_returns_no_violations() -> None:
    """When reflex_rules table doesn't exist, check returns []."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    # Do NOT apply 0005.
    violations = check_reflexes(c, workspace_id="ws", tool_name="Edit", tool_payload={}, trail=[])
    assert violations == []
    c.close()


# ============================================================
# record_fired increments counters
# ============================================================


def test_record_fired_increments_block_count(conn: sqlite3.Connection) -> None:
    _seed_rule(conn, rule_name="counted", trigger_tool="Edit", enforcement="block")
    violations = check_reflexes(
        conn, workspace_id="ws", tool_name="Edit", tool_payload={}, trail=[]
    )
    record_fired(conn, violations=violations, now_iso=iso_now())
    row = conn.execute(
        "SELECT block_count, advisory_count, last_fired_at FROM reflex_rules "
        "WHERE rule_name = 'counted'"
    ).fetchone()
    assert row["block_count"] == 1
    assert row["advisory_count"] == 0
    assert row["last_fired_at"] is not None
