"""Unit tests for the PreToolUse rule loader."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.enforcement.rule_loader import (
    MECHANICAL_TAG,
    SEMANTIC_TAG,
    filter_by_level,
    load_enforcement_rules,
)


def _setup_schema(conn: sqlite3.Connection) -> None:
    """Minimal behavior_instructions schema for the loader query."""
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


def _insert(
    conn: sqlite3.Connection,
    *,
    instruction_id: str,
    name: str,
    applies_to: list[str],
    active: int = 1,
    pinned: int = 0,
    workspace_id: str = "ws",
) -> None:
    conn.execute(
        """
        INSERT INTO behavior_instructions (
            id, workspace_id, name, rule, applies_to_json, active, pinned, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            instruction_id,
            workspace_id,
            name,
            "rule body",
            json.dumps(applies_to),
            active,
            pinned,
            "2026-05-15T00:00:00Z",
        ),
    )


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    _setup_schema(c)
    try:
        yield c
    finally:
        c.close()


def test_no_rules_returns_empty(conn: sqlite3.Connection) -> None:
    assert load_enforcement_rules(conn, "ws") == []


def test_untagged_rules_are_ignored(conn: sqlite3.Connection) -> None:
    _insert(conn, instruction_id="beh_1", name="no-tag", applies_to=["random tag"])
    assert load_enforcement_rules(conn, "ws") == []


def test_mechanical_tag_classified(conn: sqlite3.Connection) -> None:
    _insert(
        conn,
        instruction_id="beh_1",
        name="mech-rule",
        applies_to=["before Edit", MECHANICAL_TAG],
    )
    rules = load_enforcement_rules(conn, "ws")
    assert len(rules) == 1
    assert rules[0].level == "mechanical"
    assert rules[0].id == "beh_1"


def test_semantic_tag_classified(conn: sqlite3.Connection) -> None:
    _insert(
        conn,
        instruction_id="beh_2",
        name="sem-rule",
        applies_to=[SEMANTIC_TAG, "code editing workflow"],
    )
    rules = load_enforcement_rules(conn, "ws")
    assert rules[0].level == "semantic"


def test_both_tags_mechanical_wins(conn: sqlite3.Connection) -> None:
    """Mechanical wins so the cheap layer short-circuits semantic Ollama cost."""
    _insert(
        conn,
        instruction_id="beh_3",
        name="dual",
        applies_to=[MECHANICAL_TAG, SEMANTIC_TAG],
    )
    rules = load_enforcement_rules(conn, "ws")
    assert rules[0].level == "mechanical"


def test_inactive_rules_skipped(conn: sqlite3.Connection) -> None:
    _insert(
        conn,
        instruction_id="beh_1",
        name="inactive",
        applies_to=[MECHANICAL_TAG],
        active=0,
    )
    assert load_enforcement_rules(conn, "ws") == []


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    _insert(
        conn, instruction_id="beh_1", name="r", applies_to=[MECHANICAL_TAG], workspace_id="ws-a"
    )
    _insert(
        conn, instruction_id="beh_2", name="r", applies_to=[MECHANICAL_TAG], workspace_id="ws-b"
    )
    rules_a = load_enforcement_rules(conn, "ws-a")
    rules_b = load_enforcement_rules(conn, "ws-b")
    assert {r.id for r in rules_a} == {"beh_1"}
    assert {r.id for r in rules_b} == {"beh_2"}


def test_pinned_rules_come_first(conn: sqlite3.Connection) -> None:
    _insert(conn, instruction_id="beh_1", name="not-pinned", applies_to=[MECHANICAL_TAG])
    _insert(conn, instruction_id="beh_2", name="pinned", applies_to=[MECHANICAL_TAG], pinned=1)
    rules = load_enforcement_rules(conn, "ws")
    assert [r.id for r in rules] == ["beh_2", "beh_1"]


def test_malformed_applies_to_json_treated_as_no_tags(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO behavior_instructions (
            id, workspace_id, name, rule, applies_to_json, active, pinned, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("beh_x", "ws", "malformed", "rule", "{not-json", 1, 0, "2026-05-15T00:00:00Z"),
    )
    assert load_enforcement_rules(conn, "ws") == []


def test_filter_by_level_separates_layers(conn: sqlite3.Connection) -> None:
    _insert(conn, instruction_id="beh_m", name="m", applies_to=[MECHANICAL_TAG])
    _insert(conn, instruction_id="beh_s", name="s", applies_to=[SEMANTIC_TAG])
    rules = load_enforcement_rules(conn, "ws")
    assert {r.id for r in filter_by_level(rules, "mechanical")} == {"beh_m"}
    assert {r.id for r in filter_by_level(rules, "semantic")} == {"beh_s"}
