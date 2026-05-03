"""Decision lineage walker — chain assembly, cycle detection, depth bound."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.decisions.lineage import (
    DEFAULT_MAX_DEPTH,
    walk_lineage,
)


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    decision_id: str,
    title: str,
    confidence: float,
    supersedes: str | None,
    valid_from: str = "2025-01-01T00:00:00Z",
    valid_to: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO decisions
        (id, workspace_id, title, decision_text, rationale, status,
         confidence, source_episode_id, supersedes_decision_id, valid_from,
         valid_to, created_at, updated_at, pinned)
        VALUES (?, 'default', ?, 'body', '', 'active', ?, NULL, ?, ?, ?,
                '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z', 0)
        """,
        (decision_id, title, confidence, supersedes, valid_from, valid_to),
    )


def test_unknown_decision_returns_empty_chain(applied_conn: sqlite3.Connection) -> None:
    lineage = walk_lineage(applied_conn, workspace_id="default", decision_id="dec_missing")
    assert lineage.chain == []
    assert lineage.confidence_first is None
    assert lineage.confidence_latest is None


def test_single_decision_yields_one_node(applied_conn: sqlite3.Connection) -> None:
    _seed_decision(
        applied_conn,
        decision_id="dec_a",
        title="A",
        confidence=0.7,
        supersedes=None,
    )
    lineage = walk_lineage(applied_conn, workspace_id="default", decision_id="dec_a")
    assert len(lineage.chain) == 1
    assert lineage.confidence_trend == 0.0


def test_three_step_chain_walked_newest_first(applied_conn: sqlite3.Connection) -> None:
    _seed_decision(
        applied_conn, decision_id="dec_root", title="root", confidence=0.5, supersedes=None
    )
    _seed_decision(
        applied_conn, decision_id="dec_mid", title="mid", confidence=0.7, supersedes="dec_root"
    )
    _seed_decision(
        applied_conn, decision_id="dec_leaf", title="leaf", confidence=0.9, supersedes="dec_mid"
    )
    lineage = walk_lineage(applied_conn, workspace_id="default", decision_id="dec_leaf")
    assert [n.id for n in lineage.chain] == ["dec_leaf", "dec_mid", "dec_root"]
    assert lineage.confidence_first == 0.5
    assert lineage.confidence_latest == 0.9
    assert lineage.confidence_trend == 0.4


def test_negative_confidence_trend_when_chain_weakens(applied_conn: sqlite3.Connection) -> None:
    _seed_decision(
        applied_conn,
        decision_id="dec_root",
        title="root",
        confidence=0.95,
        supersedes=None,
    )
    _seed_decision(
        applied_conn,
        decision_id="dec_leaf",
        title="leaf",
        confidence=0.6,
        supersedes="dec_root",
    )
    lineage = walk_lineage(applied_conn, workspace_id="default", decision_id="dec_leaf")
    assert lineage.confidence_trend < 0
    assert lineage.confidence_trend == 0.6 - 0.95


def test_cycle_detected_does_not_loop(applied_conn: sqlite3.Connection) -> None:
    # SQLite allows self-reference here because the FK constraint is
    # (supersedes_decision_id) -> (decisions.id) — both rows exist.
    _seed_decision(
        applied_conn,
        decision_id="dec_a",
        title="A",
        confidence=0.7,
        supersedes=None,
    )
    _seed_decision(
        applied_conn,
        decision_id="dec_b",
        title="B",
        confidence=0.7,
        supersedes="dec_a",
    )
    # Now create a cycle: A supersedes B.
    applied_conn.execute("UPDATE decisions SET supersedes_decision_id = 'dec_b' WHERE id = 'dec_a'")
    lineage = walk_lineage(applied_conn, workspace_id="default", decision_id="dec_b")
    assert lineage.cycle_detected is True
    assert len(lineage.chain) == 2  # b, then a, then would loop back


def test_max_depth_truncates(applied_conn: sqlite3.Connection) -> None:
    prev: str | None = None
    for i in range(10):
        decision_id = f"dec_{i}"
        _seed_decision(
            applied_conn,
            decision_id=decision_id,
            title=f"d{i}",
            confidence=0.5 + i * 0.01,
            supersedes=prev,
        )
        prev = decision_id
    lineage = walk_lineage(applied_conn, workspace_id="default", decision_id="dec_9", max_depth=4)
    assert lineage.truncated is True
    assert len(lineage.chain) == 4


def test_default_max_depth_constant_is_reasonable() -> None:
    assert DEFAULT_MAX_DEPTH >= 8
