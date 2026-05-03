"""Pending-review surface — load + render. Empty workspace must render
nothing (envelope parity), populated workspace must include the block."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_memory_lite.retrieval.pending_review import (
    PendingReviewItem,
    PendingReviewSummary,
    load_pending_review,
    render_pending_review,
)


def test_load_pending_review_handles_legacy_schema(tmp_path: Path) -> None:
    """Pre-0023 / pre-0024 DB has no decision_candidates / insight_candidates
    tables — load_pending_review must return empty summary, not raise.
    Hub mode may route to such a legacy DB; the route must not 500."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    # Empty schema — no candidate tables at all.
    summary = load_pending_review(conn, workspace_id="default")
    assert summary.decision_candidates_count == 0
    assert summary.insight_candidates_count == 0
    assert summary.items == []
    assert summary.is_empty()
    conn.close()


def _seed_theory(conn: sqlite3.Connection, *, theory_id: str = "th_x") -> None:
    """Theories must exist before decision_candidates can FK them."""
    conn.execute(
        """
        INSERT OR IGNORE INTO theories
        (id, workspace_id, title, domain, claim, mechanism, predictions_json,
         experiment_plan, tags_json, status, supersedes_theory_id,
         source_episode_id, confidence, importance, created_at, updated_at,
         last_tested_at, validation_criteria_json,
         dependent_decision_ids_json, evidence_count, evidence_strength)
        VALUES (?, 'default', 'qa theory', 'qa', 'claim', NULL, '[]', NULL,
                '[]', 'testing', NULL, NULL, 0.5, 0.5,
                '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z', NULL,
                '[]', '[]', 0, 0.0)
        """,
        (theory_id,),
    )


def _seed_decision_candidate(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    status: str = "pending",
    theory_id: str | None = None,
) -> None:
    """Each pending candidate needs its own theory (partial unique index
    ``WHERE status='pending'`` allows at most one per theory)."""
    if theory_id is None:
        theory_id = f"th_{candidate_id}"
    _seed_theory(conn, theory_id=theory_id)
    conn.execute(
        """
        INSERT INTO decision_candidates
        (id, workspace_id, theory_id, proposed_title, proposed_decision_text,
         proposed_rationale, evidence_count, evidence_strength, confidence,
         status, created_at, updated_at)
        VALUES (?, 'default', ?, 'Adopt foo', 'body', 'why', 3, 0.8, 0.85,
                ?, '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
        """,
        (candidate_id, theory_id, status),
    )


def _seed_insight_candidate(
    conn: sqlite3.Connection, *, candidate_id: str, status: str = "pending"
) -> None:
    conn.execute(
        """
        INSERT INTO insight_candidates
        (id, workspace_id, insight_type, summary, proposed_action,
         source_episode_ids_json, confidence, status, tags_json,
         created_at, updated_at)
        VALUES (?, 'default', 'lesson', 'Try X next time', 'review',
                '[]', 0.7, ?, '[]', '2025-01-01T00:00:00Z',
                '2025-01-01T00:00:00Z')
        """,
        (candidate_id, status),
    )


def test_empty_workspace_returns_empty_summary(applied_conn: sqlite3.Connection) -> None:
    summary = load_pending_review(applied_conn, workspace_id="default")
    assert summary.is_empty()
    assert summary.total == 0
    assert render_pending_review(summary) == []


def test_populated_workspace_loads_counts_and_items(applied_conn: sqlite3.Connection) -> None:
    _seed_decision_candidate(applied_conn, candidate_id="deccand_a")
    _seed_decision_candidate(applied_conn, candidate_id="deccand_b")
    _seed_insight_candidate(applied_conn, candidate_id="inscand_a")
    summary = load_pending_review(applied_conn, workspace_id="default")
    assert summary.decision_candidates_count == 2
    assert summary.insight_candidates_count == 1
    assert summary.total == 3
    assert len(summary.items) == 3
    kinds = {item.kind for item in summary.items}
    assert kinds == {"decision_candidate", "insight_candidate"}


def test_non_pending_candidates_excluded(applied_conn: sqlite3.Connection) -> None:
    _seed_decision_candidate(applied_conn, candidate_id="deccand_p", status="pending")
    _seed_decision_candidate(applied_conn, candidate_id="deccand_r", status="rejected")
    _seed_decision_candidate(applied_conn, candidate_id="deccand_a", status="promoted")
    summary = load_pending_review(applied_conn, workspace_id="default")
    assert summary.decision_candidates_count == 1
    assert summary.items[0].id == "deccand_p"


def test_render_includes_marker_attributes() -> None:
    summary = PendingReviewSummary(
        decision_candidates_count=2,
        insight_candidates_count=1,
        items=[
            PendingReviewItem(
                kind="decision_candidate",
                id="deccand_a",
                title="Adopt foo",
                extra="from th_x",
            ),
            PendingReviewItem(
                kind="insight_candidate",
                id="inscand_a",
                title="Try X next time",
                extra="lesson",
            ),
        ],
    )
    lines = render_pending_review(summary)
    rendered = "\n".join(lines)
    assert "<pending_review" in rendered
    assert 'decision_candidates="2"' in rendered
    assert 'insight_candidates="1"' in rendered
    assert "deccand_a" in rendered
    assert "inscand_a" in rendered
    assert "Adopt foo" in rendered
    assert "Try X next time" in rendered
    assert rendered.endswith("</pending_review>")


def test_render_empty_summary_returns_no_lines() -> None:
    summary = PendingReviewSummary(
        decision_candidates_count=0, insight_candidates_count=0, items=[]
    )
    assert render_pending_review(summary) == []


def test_xml_special_chars_escaped() -> None:
    summary = PendingReviewSummary(
        decision_candidates_count=1,
        insight_candidates_count=0,
        items=[
            PendingReviewItem(
                kind="decision_candidate",
                id="deccand_x",
                title="<script> & special",
                extra="from th_x",
            ),
        ],
    )
    lines = render_pending_review(summary)
    rendered = "\n".join(lines)
    # Ensure < and & in title are escaped, not raw.
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&amp;" in rendered


def test_load_caps_per_kind_at_five(applied_conn: sqlite3.Connection) -> None:
    for i in range(7):
        _seed_decision_candidate(applied_conn, candidate_id=f"deccand_{i}")
    summary = load_pending_review(applied_conn, workspace_id="default")
    assert summary.decision_candidates_count == 7  # full count
    assert len(summary.items) == 5  # limited preview
