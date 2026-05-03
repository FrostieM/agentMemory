"""v2.2 behavioral assertion: <pending_review> envelope is actionable.

A "behavioral" test by static proxy: given a populated pending queue,
the rendered envelope must contain every piece of information an agent
needs to make a follow-up ``promote_candidate`` / ``reject_candidate``
call without going back through ``/memory/list_candidates``.

Specifically, for every pending candidate the block must surface:
* the candidate id (route parameter for promote / reject)
* the candidate kind (which endpoint to call)
* a human-readable title (for the agent to decide between candidates)
* the source theory id (for decision_candidates) so the agent can
  present the operator with provenance

Test live behavior of the full ``inject_pending_review`` injection path,
not just ``render_pending_review`` — because the actionability claim is
about what reaches the *envelope*, not just what the renderer would
emit in isolation.
"""

from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET

from agent_memory_lite.api.routes.context_post_build import inject_pending_review


def _seed_theory(conn: sqlite3.Connection, *, theory_id: str) -> None:
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
    conn: sqlite3.Connection, *, candidate_id: str, title: str, theory_id: str
) -> None:
    _seed_theory(conn, theory_id=theory_id)
    conn.execute(
        """
        INSERT INTO decision_candidates
        (id, workspace_id, theory_id, proposed_title, proposed_decision_text,
         proposed_rationale, evidence_count, evidence_strength, confidence,
         status, created_at, updated_at)
        VALUES (?, 'default', ?, ?, 'body', 'why', 3, 0.8, 0.85,
                'pending', '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
        """,
        (candidate_id, theory_id, title),
    )


def _seed_insight_candidate(conn: sqlite3.Connection, *, candidate_id: str, summary: str) -> None:
    conn.execute(
        """
        INSERT INTO insight_candidates
        (id, workspace_id, insight_type, summary, proposed_action,
         target_type, target_id, source_episode_ids_json, confidence, status,
         created_at, updated_at)
        VALUES (?, 'default', 'lesson', ?, 'try X', NULL, NULL, '[]', 0.7,
                'pending', '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
        """,
        (candidate_id, summary),
    )


def _empty_envelope() -> str:
    return "<memory_context>\n  <core_memory/>\n  <retrieved_chunks/>\n</memory_context>"


def test_envelope_carries_promote_route_inputs(applied_conn: sqlite3.Connection) -> None:
    """Pending decision-candidate row → envelope exposes id + theory_id +
    title + kind. Each of those is required for the agent to call
    /memory/decision_candidates/{id}/promote with operator-meaningful UI.
    """
    _seed_decision_candidate(
        applied_conn,
        candidate_id="dc_actionable",
        title="Adopt feedback-aware scoring",
        theory_id="th_actionable",
    )
    rendered = inject_pending_review(
        applied_conn, workspace_id="default", envelope_text=_empty_envelope()
    )
    # Parse just the <pending_review> island — surrounding envelope is opaque.
    start = rendered.find("<pending_review")
    end = rendered.find("</pending_review>") + len("</pending_review>")
    assert start >= 0, "envelope did not include <pending_review> opening tag"
    assert end > start, "envelope did not include <pending_review> closing tag"
    pr = ET.fromstring(rendered[start:end])
    assert pr.attrib["decision_candidates"] == "1"
    assert pr.attrib["insight_candidates"] == "0"
    refs = pr.findall("ref")
    assert len(refs) == 1
    ref = refs[0]
    assert ref.attrib["id"] == "dc_actionable"
    assert ref.attrib["kind"] == "decision_candidate"
    assert "th_actionable" in ref.attrib["extra"], (
        "extra must surface theory provenance so the operator sees source"
    )
    assert ref.text == "Adopt feedback-aware scoring", "title must reach the envelope verbatim"


def test_envelope_carries_insight_promote_inputs(applied_conn: sqlite3.Connection) -> None:
    """Pending insight-candidate row → envelope exposes id + insight_type +
    summary. Required for ``/memory/insight_candidates/{id}/accept``.
    """
    _seed_insight_candidate(
        applied_conn,
        candidate_id="ic_actionable",
        summary="Soft-gate replay before next live wait",
    )
    rendered = inject_pending_review(
        applied_conn, workspace_id="default", envelope_text=_empty_envelope()
    )
    start = rendered.find("<pending_review")
    end = rendered.find("</pending_review>") + len("</pending_review>")
    pr = ET.fromstring(rendered[start:end])
    assert pr.attrib["insight_candidates"] == "1"
    refs = pr.findall("ref")
    assert len(refs) == 1
    ref = refs[0]
    assert ref.attrib["id"] == "ic_actionable"
    assert ref.attrib["kind"] == "insight_candidate"
    assert ref.attrib["extra"] == "lesson"
    assert "Soft-gate replay" in (ref.text or "")


def test_mixed_queue_yields_both_kinds(applied_conn: sqlite3.Connection) -> None:
    """Mixed queue → counts on the parent attribute disagree with naive
    len(refs) only when more than 5 pending of either kind exist (per-kind
    cap). Two of each must round-trip cleanly with both refs present."""
    _seed_decision_candidate(
        applied_conn, candidate_id="dc_a", title="Decision A", theory_id="th_a"
    )
    _seed_decision_candidate(
        applied_conn, candidate_id="dc_b", title="Decision B", theory_id="th_b"
    )
    _seed_insight_candidate(applied_conn, candidate_id="ic_a", summary="Insight A")
    _seed_insight_candidate(applied_conn, candidate_id="ic_b", summary="Insight B")

    rendered = inject_pending_review(
        applied_conn, workspace_id="default", envelope_text=_empty_envelope()
    )
    start = rendered.find("<pending_review")
    end = rendered.find("</pending_review>") + len("</pending_review>")
    pr = ET.fromstring(rendered[start:end])
    assert pr.attrib["decision_candidates"] == "2"
    assert pr.attrib["insight_candidates"] == "2"
    kinds = sorted(ref.attrib["kind"] for ref in pr.findall("ref"))
    assert kinds == [
        "decision_candidate",
        "decision_candidate",
        "insight_candidate",
        "insight_candidate",
    ]
    ids = sorted(ref.attrib["id"] for ref in pr.findall("ref"))
    assert ids == ["dc_a", "dc_b", "ic_a", "ic_b"]


def test_oversize_queue_signals_truncation_via_count_attribute(
    applied_conn: sqlite3.Connection,
) -> None:
    """When pending exceeds the per-kind cap (5), the rendered refs are
    capped but the `decision_candidates` attribute still reports the full
    count — so an agent that wants the full list knows to call
    /memory/list_candidates instead of relying on the truncated refs.
    """
    for i in range(7):
        _seed_decision_candidate(
            applied_conn,
            candidate_id=f"dc_{i}",
            title=f"Candidate {i}",
            theory_id=f"th_{i}",
        )
    rendered = inject_pending_review(
        applied_conn, workspace_id="default", envelope_text=_empty_envelope()
    )
    start = rendered.find("<pending_review")
    end = rendered.find("</pending_review>") + len("</pending_review>")
    pr = ET.fromstring(rendered[start:end])
    assert pr.attrib["decision_candidates"] == "7"
    refs = pr.findall("ref")
    assert len(refs) == 5, "render must cap refs at 5 even though count says 7"
