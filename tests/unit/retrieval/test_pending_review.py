"""Pending-review surface over the canonical candidates queue."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agent_memory_lite.retrieval.pending_review import (
    load_pending_review,
)


def test_load_pending_review_handles_partial_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(db_path)
    summary = load_pending_review(conn, workspace_id="default")
    assert summary.decision_review_count == 0
    assert summary.insight_review_count == 0
    assert summary.items == []
    assert summary.is_empty()
    conn.close()


def _seed_candidate(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    kind: str,
    subject: str,
    status: str = "new",
    metadata: dict[str, object] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO candidates
        (id, workspace_id, kind, subject, predicate, object, evidence,
         confidence, importance, trust_level, temporal_json,
         write_targets_json, metadata_json, source_episode_id, status,
         created_at, updated_at)
        VALUES (?, 'default', ?, ?, 'should_review', NULL, ?,
                0.8, 0.7, 'agent_inferred', '{}',
                '[]', ?, NULL, ?,
                '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
        """,
        (candidate_id, kind, subject, subject, json.dumps(metadata or {}), status),
    )


def test_empty_workspace_returns_empty_summary(applied_conn: sqlite3.Connection) -> None:
    summary = load_pending_review(applied_conn, workspace_id="default")
    assert summary.is_empty()
    assert summary.total == 0


def test_populated_workspace_loads_counts_and_items(applied_conn: sqlite3.Connection) -> None:
    _seed_candidate(
        applied_conn,
        candidate_id="cand_dec_a",
        kind="decision",
        subject="Adopt foo",
        metadata={"theory_id": "th_a"},
    )
    _seed_candidate(
        applied_conn,
        candidate_id="cand_dec_b",
        kind="decision",
        subject="Adopt bar",
        metadata={"theory_id": "th_b"},
    )
    _seed_candidate(
        applied_conn,
        candidate_id="cand_ins_a",
        kind="insight",
        subject="Try X next time",
        metadata={"insight_type": "lesson_learned"},
    )
    summary = load_pending_review(applied_conn, workspace_id="default")
    assert summary.decision_review_count == 2
    assert summary.insight_review_count == 1
    assert summary.total == 3
    assert len(summary.items) == 3
    assert {item.kind for item in summary.items} == {"decision", "insight"}


def test_non_new_candidates_excluded(applied_conn: sqlite3.Connection) -> None:
    _seed_candidate(applied_conn, candidate_id="cand_new", kind="decision", subject="new")
    _seed_candidate(
        applied_conn,
        candidate_id="cand_rej",
        kind="decision",
        subject="rejected",
        status="rejected",
    )
    _seed_candidate(
        applied_conn,
        candidate_id="cand_pro",
        kind="decision",
        subject="promoted",
        status="promoted",
    )
    summary = load_pending_review(applied_conn, workspace_id="default")
    assert summary.decision_review_count == 1
    assert summary.items[0].id == "cand_new"


def test_load_caps_per_kind_at_five(applied_conn: sqlite3.Connection) -> None:
    for i in range(7):
        _seed_candidate(
            applied_conn,
            candidate_id=f"cand_{i}",
            kind="decision",
            subject=f"Candidate {i}",
        )
    summary = load_pending_review(applied_conn, workspace_id="default")
    assert summary.decision_review_count == 7
    assert len(summary.items) == 5
