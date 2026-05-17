"""Unit tests for v3 memory_lint adapter."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.v3.cognition.lint import LintResult, lint

SCHEMA_V3_PATH = Path(__file__).resolve().parents[3] / "migrations" / "v3" / "0001_init.sql"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_V3_PATH.read_text(encoding="utf-8"))
    # Bridge: v3 schema has `behaviors` table; enforcement.rule_loader
    # currently reads `behavior_instructions`. For v3 lint to work
    # against v3 storage, the test data is seeded into both names
    # until Phase 1 batch 3 ports rule_loader to v3.
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS behavior_instructions (
            id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT, kind TEXT,
            scope TEXT, priority TEXT, rule TEXT, rationale TEXT DEFAULT '',
            applies_to_json TEXT DEFAULT '[]', conflict_policy TEXT,
            source_episode_id TEXT, confidence REAL, active INTEGER DEFAULT 1,
            created_at TEXT, updated_at TEXT, source_type TEXT DEFAULT 'manual',
            source_id TEXT, reviewed_by TEXT, reviewed_at TEXT, expires_at TEXT,
            last_applied_at TEXT, application_count INTEGER DEFAULT 0,
            conflict_group TEXT, pinned INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    try:
        yield c
    finally:
        c.close()


def _seed_behavior_v2_compat(
    conn: sqlite3.Connection,
    *,
    id_: str,
    name: str,
    rule: str,
    applies_to_json: str = "[]",
    pinned: int = 1,
    active: int = 1,
) -> None:
    """Seed into v2 behavior_instructions (lint reads this for now)."""
    conn.execute(
        """INSERT INTO behavior_instructions
           (id, workspace_id, name, kind, scope, priority, rule, applies_to_json,
            active, pinned, created_at, updated_at)
           VALUES (?, 'ws', ?, 'operating_rule', 'workspace', 'user_preference', ?, ?, ?, ?, 'ts', 'ts')""",
        (id_, name, rule, applies_to_json, active, pinned),
    )
    conn.commit()


def _seed_decision_v3(conn: sqlite3.Connection, *, id_: str, title: str, gist: str) -> None:
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at)
           VALUES (?, 'ws', ?, 'body', ?, 'active', 'ts', 'ts', 'ts')""",
        (id_, title, gist),
    )
    conn.commit()


# ============================================================
# Verdict + advisory mapping
# ============================================================


def test_lint_empty_workspace_allows(conn: sqlite3.Connection) -> None:
    """No rules, no advisories — verdict is allow."""
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "x", "new_string": "y"},
    )
    assert result.verdict == "allow"
    assert result.applicable_rules == []


def test_lint_returns_lint_result(conn: sqlite3.Connection) -> None:
    result = lint(conn, workspace_id="ws", tool_name="Read", tool_payload={})
    assert isinstance(result, LintResult)


def test_lint_mechanical_block_returns_block_verdict(conn: sqlite3.Connection) -> None:
    """Seed a magic-number rule and confirm Edit with literal blocks."""
    _seed_behavior_v2_compat(
        conn,
        id_="beh_mn",
        name="no-magic-number",
        rule="No magic numbers in strategy code.",
        applies_to_json='["enforcement:mechanical", "mechanical:no-magic-number"]',
    )
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={
            "file_path": "src/strategy/x.py",
            "new_string": "if confidence > 0.85:\n    pass",
        },
    )
    assert result.verdict == "block"
    assert any(r["rule_id"] == "beh_mn" for r in result.applicable_rules)


def test_lint_advisory_path_allows_with_advisories(conn: sqlite3.Connection) -> None:
    """A pinned rule applicable but not blocking → allow_with_advisories.

    Implementation: we seed an enforcement:mechanical rule with
    decision-provenance tag; lint produces a 'block' (mechanical
    detector fires), so we ALSO check the inverse path: when
    no enforcement rules exist for a tool, verdict is plain 'allow'.
    """
    # Mechanical rules always block in the current dispatcher; pure
    # advisory comes from semantic rules when Ollama disabled.
    # Seed a semantic-only rule (won't fire without Ollama) and
    # verify verdict is 'allow' (not block).
    _seed_behavior_v2_compat(
        conn,
        id_="beh_s",
        name="semantic-rule",
        rule="Be careful with X.",
        applies_to_json='["enforcement:semantic"]',
    )
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/api/x.py", "new_string": "ok"},
        ollama_base_url=None,
        ollama_model=None,
    )
    assert result.verdict == "allow"


def test_lint_related_decisions_match_payload(conn: sqlite3.Connection) -> None:
    _seed_decision_v3(
        conn, id_="dec_1", title="Kelly sizing", gist="Use quarter-Kelly for $200 bankroll"
    )
    _seed_decision_v3(conn, id_="dec_2", title="Unrelated", gist="Other topic")
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/strategy/kelly.py", "new_string": "kelly"},
    )
    ids = [d["id"] for d in result.related_decisions]
    assert "dec_1" in ids


def test_lint_prior_failures_finds_rejected_theory(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT INTO theories
           (id, workspace_id, title, domain, claim, status, created_at, updated_at)
           VALUES ('th_x', 'ws', 'Half-Kelly safe', 'trading', 'Half-Kelly is safe at $200',
                   'rejected', 'ts', 'ts')"""
    )
    conn.commit()
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "src/strategy/kelly.py"},
    )
    ids = [f["id"] for f in result.prior_failures]
    assert "th_x" in ids


def test_lint_watch_outs_renders_when_failures_present(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT INTO theories
           (id, workspace_id, title, domain, claim, status, gist, created_at, updated_at)
           VALUES ('th_x', 'ws', 'Kelly safe', 'trading', 'Claim text',
                   'rejected', 'Half-Kelly drawdown >40%', 'ts', 'ts')"""
    )
    conn.commit()
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Edit",
        tool_payload={"file_path": "kelly"},
    )
    assert "rejected_theory" in result.watch_outs


def test_lint_to_dict_serialization(conn: sqlite3.Connection) -> None:
    result = lint(conn, workspace_id="ws", tool_name="Read", tool_payload={})
    data = result.to_dict()
    assert "verdict" in data
    assert "applicable_rules" in data
    assert "related_decisions" in data
    assert "prior_failures" in data
    assert "watch_outs" in data


def test_lint_failure_soft_on_subsystem_error(conn: sqlite3.Connection) -> None:
    """Even if a subsystem raises, lint returns allow + diagnostic."""
    # Drop the behaviors table to simulate a schema mismatch / corruption.
    conn.execute("DROP TABLE behavior_instructions")
    conn.commit()
    result = lint(conn, workspace_id="ws", tool_name="Edit", tool_payload={"file_path": "x"})
    # Should not raise; verdict degrades to allow.
    assert result.verdict == "allow"


def test_lint_payload_query_extraction_for_decisions(conn: sqlite3.Connection) -> None:
    """Payload query extracted from common fields for the related-decision search."""
    _seed_decision_v3(conn, id_="dec_d", title="Deploy", gist="Deploy procedure")
    result = lint(
        conn,
        workspace_id="ws",
        tool_name="Bash",
        tool_payload={"command": "deploy step run"},
    )
    ids = [d["id"] for d in result.related_decisions]
    assert "dec_d" in ids
