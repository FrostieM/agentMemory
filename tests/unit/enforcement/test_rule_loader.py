"""Unit tests for the PreToolUse rule loader."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.enforcement.mechanical_dispatch import _DETECTOR_REGISTRY
from agent_memory_lite.enforcement.rule_loader import (
    _KNOWN_MECHANICAL_TAGS,
    MECHANICAL_TAG,
    OPT_OUT_TAG,
    SEMANTIC_TAG,
    filter_by_level,
    load_enforcement_rules,
)


def _setup_schema(conn: sqlite3.Connection) -> None:
    """Minimal behaviors schema for the loader query."""
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


def _insert(
    conn: sqlite3.Connection,
    *,
    instruction_id: str,
    name: str,
    applies_to: list[str],
    active: int = 1,
    pinned: int = 1,
    workspace_id: str = "ws",
) -> None:
    conn.execute(
        """
        INSERT INTO behaviors (
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


def test_unpinned_rules_skipped(conn: sqlite3.Connection) -> None:
    """Only pinned rules are enforced — pinning is the operator's signal."""
    _insert(conn, instruction_id="beh_1", name="unpinned", applies_to=[], pinned=0)
    assert load_enforcement_rules(conn, "ws") == []


def test_inactive_rules_skipped(conn: sqlite3.Connection) -> None:
    _insert(conn, instruction_id="beh_1", name="inactive", applies_to=[], active=0)
    assert load_enforcement_rules(conn, "ws") == []


def test_pinned_rule_without_tag_defaults_to_semantic(conn: sqlite3.Connection) -> None:
    """The pivot: every pinned rule is enforced. Unknown shape => semantic."""
    _insert(conn, instruction_id="beh_1", name="bare", applies_to=["before commit"])
    rules = load_enforcement_rules(conn, "ws")
    assert len(rules) == 1
    assert rules[0].level == "semantic"


def test_opt_out_tag_disables_enforcement(conn: sqlite3.Connection) -> None:
    """enforcement:none tag keeps a rule as foreground reminder only."""
    _insert(conn, instruction_id="beh_1", name="reminder-only", applies_to=[OPT_OUT_TAG])
    assert load_enforcement_rules(conn, "ws") == []


def test_mechanical_detector_tag_routes_to_mechanical(conn: sqlite3.Connection) -> None:
    _insert(
        conn,
        instruction_id="beh_1",
        name="mech-rule",
        applies_to=["mechanical:no-magic-number", "before Edit"],
    )
    rules = load_enforcement_rules(conn, "ws")
    assert rules[0].level == "mechanical"


def test_impact_check_before_read_tag_routes_to_mechanical(conn: sqlite3.Connection) -> None:
    """Regression: a behavior tagged only with the impact-check-before-read
    detector tag must route mechanical. The tag was registered in the
    dispatcher but missing from the classifier's known set, so such rules
    silently fell through to the slow LLM (semantic) path and the
    read-before-impact-check invariant was never mechanically enforced.
    """
    _insert(
        conn,
        instruction_id="beh_ic",
        name="impact-check-before-read",
        applies_to=["mechanical:impact-check-before-read"],
    )
    rules = load_enforcement_rules(conn, "ws")
    assert len(rules) == 1
    assert rules[0].level == "mechanical"


def test_known_mechanical_tags_match_dispatch_registry() -> None:
    """Drift guard: the classifier's known-tag set must equal the dispatcher's
    registered detector tags exactly. If they diverge, a rule is either routed
    to a detector that does not exist or starved of one that does -- both silent.
    Pin them so adding/removing a detector forces both sides to stay in sync.
    """
    assert frozenset(_DETECTOR_REGISTRY) == _KNOWN_MECHANICAL_TAGS


def test_explicit_mechanical_tag_overrides_classification(conn: sqlite3.Connection) -> None:
    _insert(
        conn,
        instruction_id="beh_1",
        name="explicit-mech",
        applies_to=[MECHANICAL_TAG, "before Edit"],
    )
    assert load_enforcement_rules(conn, "ws")[0].level == "mechanical"


def test_explicit_semantic_tag_overrides_classification(conn: sqlite3.Connection) -> None:
    _insert(
        conn,
        instruction_id="beh_1",
        name="explicit-sem",
        applies_to=[SEMANTIC_TAG, "mechanical:no-magic-number"],
    )
    # Explicit SEMANTIC_TAG wins over detector tag.
    assert load_enforcement_rules(conn, "ws")[0].level == "semantic"


def test_opt_out_wins_over_other_tags(conn: sqlite3.Connection) -> None:
    """If both opt-out and mechanical detector tags present, opt-out wins."""
    _insert(
        conn,
        instruction_id="beh_1",
        name="conflicted",
        applies_to=[OPT_OUT_TAG, "mechanical:no-magic-number"],
    )
    assert load_enforcement_rules(conn, "ws") == []


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    _insert(conn, instruction_id="beh_1", name="r", applies_to=[], workspace_id="ws-a")
    _insert(conn, instruction_id="beh_2", name="r", applies_to=[], workspace_id="ws-b")
    rules_a = load_enforcement_rules(conn, "ws-a")
    rules_b = load_enforcement_rules(conn, "ws-b")
    assert {r.id for r in rules_a} == {"beh_1"}
    assert {r.id for r in rules_b} == {"beh_2"}


def test_malformed_applies_to_json_defaults_to_semantic(conn: sqlite3.Connection) -> None:
    """Pinned rule with corrupt applies_to is still enforced — semantic layer."""
    conn.execute(
        """
        INSERT INTO behaviors (
            id, workspace_id, name, rule, applies_to_json, active, pinned, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("beh_x", "ws", "malformed", "rule", "{not-json", 1, 1, "2026-05-15T00:00:00Z"),
    )
    rules = load_enforcement_rules(conn, "ws")
    assert len(rules) == 1
    assert rules[0].level == "semantic"


def test_filter_by_level_separates_layers(conn: sqlite3.Connection) -> None:
    _insert(
        conn,
        instruction_id="beh_m",
        name="m",
        applies_to=["mechanical:no-magic-number"],
    )
    _insert(conn, instruction_id="beh_s", name="s", applies_to=["before commit"])
    rules = load_enforcement_rules(conn, "ws")
    assert {r.id for r in filter_by_level(rules, "mechanical")} == {"beh_m"}
    assert {r.id for r in filter_by_level(rules, "semantic")} == {"beh_s"}


def test_multiple_pinned_returned_newest_first(conn: sqlite3.Connection) -> None:
    """Order is updated_at DESC so the operator's freshest rules dominate."""
    conn.execute(
        """
        INSERT INTO behaviors (
            id, workspace_id, name, rule, applies_to_json, active, pinned, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("beh_old", "ws", "old", "rule", "[]", 1, 1, "2026-01-01T00:00:00Z"),
    )
    conn.execute(
        """
        INSERT INTO behaviors (
            id, workspace_id, name, rule, applies_to_json, active, pinned, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("beh_new", "ws", "new", "rule", "[]", 1, 1, "2026-05-15T00:00:00Z"),
    )
    rules = load_enforcement_rules(conn, "ws")
    assert [r.id for r in rules] == ["beh_new", "beh_old"]
