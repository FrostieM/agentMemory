"""Trust-gate invariant: reflective compaction NEVER inserts insights.

Critical regression guard. If this test starts failing, someone wired a
fast-path that bypasses the operator review and broke the trust gate.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.compaction.lesson_proposal import LessonProposal
from agent_memory_lite.compaction.lesson_review import write_lesson_candidates


def _insights_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM insights").fetchone()[0])


def _insight_review_count(conn: sqlite3.Connection, *, status: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM candidates WHERE kind = 'insight' AND status = ?", (status,)
        ).fetchone()[0]
    )


def _proposal(*, summary: str = "lesson", confidence: float = 0.7) -> LessonProposal:
    return LessonProposal(
        insight_type="lesson_learned",
        summary=summary,
        proposed_action="follow up",
        source_episode_ids=("ep_a", "ep_b", "ep_c", "ep_d"),
        confidence=confidence,
    )


def test_write_lesson_candidates_creates_pending_rows(applied_conn: sqlite3.Connection) -> None:
    before = _insights_count(applied_conn)
    ids = write_lesson_candidates(
        applied_conn,
        workspace_id="default",
        proposals=[_proposal(summary=f"lesson {i}") for i in range(3)],
    )
    assert len(ids) == 3
    assert _insight_review_count(applied_conn, status="new") == 3
    # Trust-gate guard: insights table must be untouched.
    assert _insights_count(applied_conn) == before


def test_empty_proposals_with_zero_discarded_does_nothing(
    applied_conn: sqlite3.Connection,
) -> None:
    ids = write_lesson_candidates(
        applied_conn, workspace_id="default", proposals=[], discarded_count=0
    )
    assert ids == []
    audit_rows = applied_conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action LIKE 'compaction.lesson%'"
    ).fetchone()
    assert int(audit_rows[0]) == 0


def test_audit_row_emitted_per_batch_only_once(applied_conn: sqlite3.Connection) -> None:
    write_lesson_candidates(
        applied_conn,
        workspace_id="default",
        proposals=[_proposal(summary=f"lesson {i}") for i in range(5)],
    )
    audit_rows = applied_conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'compaction.lesson_proposed'"
    ).fetchone()
    assert int(audit_rows[0]) == 1


def test_discarded_count_emits_separate_audit_row(applied_conn: sqlite3.Connection) -> None:
    write_lesson_candidates(
        applied_conn,
        workspace_id="default",
        proposals=[],
        discarded_count=3,
    )
    audit_rows = applied_conn.execute(
        "SELECT action FROM audit_log WHERE target_type = 'workspace' AND target_id = 'default'"
    ).fetchall()
    actions = [str(row[0]) for row in audit_rows]
    assert "compaction.lesson_rejected_low_support" in actions
    # No 'compaction.lesson_proposed' when there are no proposals.
    assert "compaction.lesson_proposed" not in actions
