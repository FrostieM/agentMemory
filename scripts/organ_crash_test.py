"""End-to-end crash test for the v3.0.0-final memory-organ on real data.

Operator workflow:
  1. Copy a real workspace DB to a sandbox path.
  2. Point this script at the sandbox + the source workspace_id.
  3. Read the JSON / text report; non-zero exit = at least one test failed.

The script runs 13 named tests across all 7 organ phases plus integration
glue. Every test is read-only on the SOURCE DB; mutations land only in the
sandbox copy. Tests are independent: each manages its own seeded fixtures,
so one failure does not cascade.

Usage:
    python scripts/organ_crash_test.py --db <sandbox.db> --workspace <id>
    python scripts/organ_crash_test.py --db <sandbox.db> --workspace <id> --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from agent_memory_lite.cognition.brief import compose_brief  # noqa: E402
from agent_memory_lite.cognition.outcome_recompute import refresh_workspace  # noqa: E402
from agent_memory_lite.cognition.self_model import (  # noqa: E402
    load_self_model,
    refresh_self_model,
)
from agent_memory_lite.compaction.promote_insight_to_behavior import (  # noqa: E402
    promote_eligible_insights,
)
from agent_memory_lite.config.settings import Settings  # noqa: E402
from agent_memory_lite.enforcement.reflex_check import check_reflexes  # noqa: E402
from agent_memory_lite.maintenance.hebbian_pass import distill_workspace  # noqa: E402
from agent_memory_lite.maintenance.organ_pass import run_organ_pass  # noqa: E402
from agent_memory_lite.retrieval.causal_extractor import _upsert_causal_link  # noqa: E402
from agent_memory_lite.retrieval.recall import recall  # noqa: E402
from agent_memory_lite.storage.reader import list_kind  # noqa: E402
from agent_memory_lite.utils.time import iso_now  # noqa: E402

GREEN = "\033[32m" if sys.stdout.isatty() else ""
RED = "\033[31m" if sys.stdout.isatty() else ""
YELLOW = "\033[33m" if sys.stdout.isatty() else ""
RESET = "\033[0m" if sys.stdout.isatty() else ""


@dataclass(slots=True)
class TestResult:
    name: str
    phase: str
    status: str  # "pass" | "fail" | "skip"
    detail: str = ""
    metrics: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class CrashReport:
    db_path: str
    workspace_id: str
    results: list[TestResult] = field(default_factory=list)

    def add(self, result: TestResult) -> None:
        self.results.append(result)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == "pass")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "fail")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == "skip")


# ============================================================
# Phase 1 -- outcome_score
# ============================================================


def test_phase1_archived_drops_to_minus_one(conn: sqlite3.Connection, ws: str) -> TestResult:
    """Mark a fresh decision as archived -> recompute -> assert outcome=-1.0."""
    test_id = "crash_test_archived_dec"
    conn.execute("DELETE FROM decisions WHERE id = ?", (test_id,))
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, status, valid_from,
            created_at, updated_at, outcome_score, pinned, feedback_ewma)
           VALUES (?, ?, 'Crash test', 'body', 'archived', ?, ?, ?, 0.0, 0, 0.5)""",
        (test_id, ws, iso_now(), iso_now(), iso_now()),
    )
    conn.commit()
    refresh_workspace(conn, workspace_id=ws, now_iso=iso_now())
    conn.commit()
    row = conn.execute("SELECT outcome_score FROM decisions WHERE id = ?", (test_id,)).fetchone()
    conn.execute("DELETE FROM decisions WHERE id = ?", (test_id,))
    conn.commit()
    if row is None:
        return TestResult("phase1_archived", "Phase 1", "fail", "test row missing after recompute")
    score = float(row[0])
    if score == -1.0:
        return TestResult(
            "phase1_archived",
            "Phase 1",
            "pass",
            f"archived decision recomputed to {score}",
            {"outcome_score": score},
        )
    return TestResult(
        "phase1_archived",
        "Phase 1",
        "fail",
        f"expected -1.0, got {score}",
        {"outcome_score": score},
    )


def test_phase1_superseded_drops_below_zero(conn: sqlite3.Connection, ws: str) -> TestResult:
    """A decision marked status='superseded' (the OLD row whose ID
    appears in some other row's supersedes_decision_id) should
    recompute to outcome < 0.

    Earlier crash-test version seeded a row with supersedes_decision_id
    set on the test row itself -- that's the NEW row pointing AT an
    older one, which is NOT a superseded signal. The fix in
    outcome_recompute.py corrected this semantic; we update the test
    to match: the row that GETS replaced is the one that carries
    status='superseded' (writer pipeline sets this via close_decision).
    """
    test_id = "crash_test_superseded"
    parent_id = "crash_test_super_parent"
    conn.execute("DELETE FROM decisions WHERE id IN (?, ?)", (test_id, parent_id))
    # OLD row: status='superseded' -- the row that GOT replaced.
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, status, valid_from,
            created_at, updated_at, outcome_score, pinned, feedback_ewma)
           VALUES (?, ?, 'old', 'body', 'superseded', ?, ?, ?, 0.0, 0, 0.5)""",
        (test_id, ws, iso_now(), iso_now(), iso_now()),
    )
    # NEW row: active, points to OLD via supersedes_decision_id.
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, status, valid_from,
            created_at, updated_at, outcome_score, pinned, feedback_ewma,
            supersedes_decision_id)
           VALUES (?, ?, 'new', 'body', 'active', ?, ?, ?, 0.0, 0, 0.5, ?)""",
        (parent_id, ws, iso_now(), iso_now(), iso_now(), test_id),
    )
    conn.commit()
    refresh_workspace(conn, workspace_id=ws, now_iso=iso_now())
    conn.commit()
    row = conn.execute("SELECT outcome_score FROM decisions WHERE id = ?", (test_id,)).fetchone()
    conn.execute("DELETE FROM decisions WHERE id IN (?, ?)", (test_id, parent_id))
    conn.commit()
    if row is None:
        return TestResult("phase1_superseded", "Phase 1", "fail", "test row missing")
    score = float(row[0])
    if score < 0.0:
        return TestResult(
            "phase1_superseded",
            "Phase 1",
            "pass",
            f"superseded score {score} (< 0 as expected)",
            {"outcome_score": score},
        )
    return TestResult(
        "phase1_superseded",
        "Phase 1",
        "fail",
        f"expected < 0, got {score}",
        {"outcome_score": score},
    )


# ============================================================
# Phase 2 -- Hebbian HeLa-Mem gate
# ============================================================


def test_phase2_hela_mem_gate_blocks_double_zero(conn: sqlite3.Connection, ws: str) -> TestResult:
    """Two STRICTLY negative-outcome items should NOT be linked (HeLa-Mem gate).

    Earlier semantics used ``<= 0`` which also banned neutral [0, 0] pairs.
    Empirical probe on copyBot showed that froze the Hebbian graph on
    fresh workspaces. New gate only blocks pairs rooted in demonstrated
    failure (both strictly < 0).
    """
    a, b = "crash_hela_a", "crash_hela_b"
    qh = "crash_hela_query_hash"
    conn.execute("DELETE FROM decisions WHERE id IN (?, ?)", (a, b))
    conn.execute("DELETE FROM retrieval_coactivation WHERE query_hash = ?", (qh,))
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, ?, 'A', 'b', 'active', ?, ?, ?, -0.5, 0)""",
        (a, ws, iso_now(), iso_now(), iso_now()),
    )
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, ?, 'B', 'b', 'active', ?, ?, ?, -0.3, 0)""",
        (b, ws, iso_now(), iso_now(), iso_now()),
    )
    for rank, item in enumerate((a, b), start=1):
        conn.execute(
            """INSERT INTO retrieval_coactivation
               (id, workspace_id, query_hash, item_kind, item_id, rank, created_at)
               VALUES (?, ?, ?, 'decision', ?, ?, ?)""",
            (f"crash_coact_{item}", ws, qh, item, rank, iso_now()),
        )
    conn.commit()
    # Count edges BEFORE distill.
    edges_before = conn.execute(
        "SELECT COUNT(*) FROM soft_edges WHERE workspace_id = ? AND edge_kind = 'co_retrieved' "
        "AND (src_qualified_name = ? OR dst_qualified_name = ?)",
        (ws, f"decision:{a}", f"decision:{a}"),
    ).fetchone()[0]
    up, gated = distill_workspace(conn, workspace_id=ws, outcome_gate=True)
    conn.commit()
    edges_after = conn.execute(
        "SELECT COUNT(*) FROM soft_edges WHERE workspace_id = ? AND edge_kind = 'co_retrieved' "
        "AND (src_qualified_name = ? OR dst_qualified_name = ?)",
        (ws, f"decision:{a}", f"decision:{a}"),
    ).fetchone()[0]
    # Cleanup.
    conn.execute("DELETE FROM decisions WHERE id IN (?, ?)", (a, b))
    conn.execute("DELETE FROM retrieval_coactivation WHERE query_hash = ?", (qh,))
    conn.commit()
    edge_delta = edges_after - edges_before
    if edge_delta == 0 and gated >= 1:
        return TestResult(
            "phase2_hela_mem_gate",
            "Phase 2",
            "pass",
            f"double-zero pair gated (delta={edge_delta}, gated={gated})",
            {"edges_added": edge_delta, "edges_gated": gated},
        )
    return TestResult(
        "phase2_hela_mem_gate",
        "Phase 2",
        "fail",
        f"expected 0 edges + gate fire, got delta={edge_delta}, gated={gated}",
        {"edges_added": edge_delta, "edges_gated": gated, "upserted": up},
    )


def test_phase2_one_positive_lets_pair_through(conn: sqlite3.Connection, ws: str) -> TestResult:
    """If at least one side has outcome > 0, the gate allows the link."""
    a, b = "crash_hela_pos_a", "crash_hela_pos_b"
    qh = "crash_hela_pos_query"
    conn.execute("DELETE FROM decisions WHERE id IN (?, ?)", (a, b))
    conn.execute("DELETE FROM retrieval_coactivation WHERE query_hash = ?", (qh,))
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, ?, 'A', 'b', 'active', ?, ?, ?, 0.6, 0)""",
        (a, ws, iso_now(), iso_now(), iso_now()),
    )
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, ?, 'B', 'b', 'active', ?, ?, ?, 0.0, 0)""",
        (b, ws, iso_now(), iso_now(), iso_now()),
    )
    for rank, item in enumerate((a, b), start=1):
        conn.execute(
            """INSERT INTO retrieval_coactivation
               (id, workspace_id, query_hash, item_kind, item_id, rank, created_at)
               VALUES (?, ?, ?, 'decision', ?, ?, ?)""",
            (f"crash_coact_pos_{item}", ws, qh, item, rank, iso_now()),
        )
    conn.commit()
    up, gated = distill_workspace(conn, workspace_id=ws, outcome_gate=True)
    conn.commit()
    # Cleanup including the edge.
    src = f"decision:{min(a, b)}"
    dst = f"decision:{max(a, b)}"
    conn.execute(
        "DELETE FROM soft_edges WHERE workspace_id = ? AND src_qualified_name = ? "
        "AND dst_qualified_name = ? AND edge_kind = 'co_retrieved'",
        (ws, src, dst),
    )
    conn.execute("DELETE FROM decisions WHERE id IN (?, ?)", (a, b))
    conn.execute("DELETE FROM retrieval_coactivation WHERE query_hash = ?", (qh,))
    conn.commit()
    if up == 1 and gated == 0:
        return TestResult(
            "phase2_one_positive_passes_gate",
            "Phase 2",
            "pass",
            f"upserted={up}, gated={gated}",
            {"upserted": up, "gated": gated},
        )
    return TestResult(
        "phase2_one_positive_passes_gate",
        "Phase 2",
        "fail",
        f"expected upserted=1 gated=0, got upserted={up} gated={gated}",
    )


# ============================================================
# Phase 3 -- insight -> behavior promotion
# ============================================================


def test_phase3_promote_high_confidence_insight(conn: sqlite3.Connection, ws: str) -> TestResult:
    """Confidence 0.8 + surface_count 2 -> promotes to pinned behavior."""
    ins_id = "crash_test_promote_ins"
    conn.execute("DELETE FROM insights WHERE id = ?", (ins_id,))
    conn.execute("DELETE FROM behaviors WHERE source_id = ?", (ins_id,))
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, status,
            confidence, surface_count, created_at, updated_at)
           VALUES (?, ?, 'consolidation', 'crash test insight body',
                   'crash test insight', 'candidate', 0.85, 3, ?, ?)""",
        (ins_id, ws, iso_now(), iso_now()),
    )
    conn.commit()
    stats = promote_eligible_insights(conn, workspace_id=ws)
    conn.commit()
    insight_status = conn.execute("SELECT status FROM insights WHERE id = ?", (ins_id,)).fetchone()
    behavior_row = conn.execute(
        "SELECT pinned, source_type FROM behaviors WHERE source_id = ?", (ins_id,)
    ).fetchone()
    # Cleanup.
    conn.execute("DELETE FROM insights WHERE id = ?", (ins_id,))
    conn.execute("DELETE FROM behaviors WHERE source_id = ?", (ins_id,))
    conn.commit()
    if (
        stats.promoted >= 1
        and insight_status is not None
        and insight_status[0] == "promoted"
        and behavior_row is not None
        and behavior_row[0] == 1
        and behavior_row[1] == "insight"
    ):
        return TestResult(
            "phase3_promote_insight",
            "Phase 3",
            "pass",
            f"promoted insight -> pinned behavior (source_type={behavior_row[1]})",
            {"promoted": stats.promoted},
        )
    return TestResult(
        "phase3_promote_insight",
        "Phase 3",
        "fail",
        f"promoted={stats.promoted}, status={insight_status}, behavior={behavior_row}",
    )


def test_phase3_low_confidence_does_not_promote(conn: sqlite3.Connection, ws: str) -> TestResult:
    """Confidence 0.5 -> gate keeps it (no promotion)."""
    ins_id = "crash_test_low_conf_ins"
    conn.execute("DELETE FROM insights WHERE id = ?", (ins_id,))
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, status,
            confidence, surface_count, created_at, updated_at)
           VALUES (?, ?, 'consolidation', 'low confidence', 'candidate',
                   0.5, 5, ?, ?)""",
        (ins_id, ws, iso_now(), iso_now()),
    )
    conn.commit()
    stats = promote_eligible_insights(conn, workspace_id=ws)
    conn.commit()
    behavior_row = conn.execute("SELECT 1 FROM behaviors WHERE source_id = ?", (ins_id,)).fetchone()
    conn.execute("DELETE FROM insights WHERE id = ?", (ins_id,))
    conn.commit()
    if stats.promoted == 0 and behavior_row is None:
        return TestResult(
            "phase3_low_conf_gate",
            "Phase 3",
            "pass",
            "low-confidence insight correctly not promoted",
        )
    return TestResult(
        "phase3_low_conf_gate",
        "Phase 3",
        "fail",
        f"expected promoted=0, got {stats.promoted}",
    )


# ============================================================
# Phase 4 -- reflex check
# ============================================================


def test_phase4_reflex_blocks_edit_without_impact_check(
    conn: sqlite3.Connection, ws: str
) -> TestResult:
    """Empty trail + Edit on .py -> reflex fires."""
    violations = check_reflexes(
        conn,
        workspace_id=ws,
        tool_name="Edit",
        tool_payload={"file_path": "src/foo.py"},
        trail=[],
        block_override=False,
    )
    matching = [v for v in violations if v.rule_name == "edit-requires-impact-check"]
    if matching:
        return TestResult(
            "phase4_reflex_blocks",
            "Phase 4",
            "pass",
            f"reflex fired (enforcement={matching[0].enforcement})",
            {"violations": len(violations)},
        )
    return TestResult(
        "phase4_reflex_blocks",
        "Phase 4",
        "fail",
        f"reflex did not fire; got {len(violations)} violations",
    )


def test_phase4_reflex_allows_when_precondition_met(
    conn: sqlite3.Connection, ws: str
) -> TestResult:
    """Trail with memory_impact_check -> reflex does NOT fire."""
    violations = check_reflexes(
        conn,
        workspace_id=ws,
        tool_name="Edit",
        tool_payload={"file_path": "src/foo.py"},
        trail=["Read", "memory_impact_check", "Edit"],
        block_override=False,
    )
    matching = [v for v in violations if v.rule_name == "edit-requires-impact-check"]
    if not matching:
        return TestResult(
            "phase4_reflex_skipped",
            "Phase 4",
            "pass",
            "reflex correctly skipped when impact_check in trail",
        )
    return TestResult(
        "phase4_reflex_skipped",
        "Phase 4",
        "fail",
        f"reflex fired despite precondition: {matching[0].advisory}",
    )


# ============================================================
# Phase 5 -- self-model
# ============================================================


def test_phase5_self_model_refresh_writes_row(conn: sqlite3.Connection, ws: str) -> TestResult:
    """refresh_self_model writes a 50-150 word narrative."""
    model = refresh_self_model(conn, workspace_id=ws)
    conn.commit()
    if model is None:
        return TestResult("phase5_self_model", "Phase 5", "fail", "refresh returned None")
    loaded = load_self_model(conn, workspace_id=ws)
    if loaded is None:
        return TestResult("phase5_self_model", "Phase 5", "fail", "row missing after refresh")
    word_count = len(loaded.identity_text.split())
    if 50 <= word_count <= 150:
        return TestResult(
            "phase5_self_model",
            "Phase 5",
            "pass",
            f"narrative {word_count} words, via {loaded.refreshed_via}",
            {"words": word_count, "refreshed_via": loaded.refreshed_via},
        )
    return TestResult(
        "phase5_self_model",
        "Phase 5",
        "fail",
        f"word count {word_count} out of [50, 150] band",
        {"words": word_count},
    )


# ============================================================
# Phase 6 -- bi-temporal facts
# ============================================================


def test_phase6_expired_concept_excluded_today(conn: sqlite3.Connection, ws: str) -> TestResult:
    """Concept with valid_to in the past must not surface in list_kind(as_of=now)."""
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    cid = "crash_test_expired_concept"
    conn.execute("DELETE FROM concepts WHERE id = ?", (cid,))
    past = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    very_past = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    conn.execute(
        """INSERT INTO concepts
           (id, workspace_id, name, kind, definition, definition_one_line,
            aliases_json, created_at, updated_at, valid_from, valid_to)
           VALUES (?, ?, 'crash_concept', 'term', 'def', 'def', '[]', ?, ?, ?, ?)""",
        (cid, ws, iso_now(), iso_now(), very_past, past),
    )
    conn.commit()
    today_rows = list_kind(conn, workspace_id=ws, kind="concept", limit=500)
    today_ids = {c["id"] for c in today_rows}
    middle = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    historical_rows = list_kind(conn, workspace_id=ws, kind="concept", limit=500, as_of=middle)
    historical_ids = {c["id"] for c in historical_rows}
    conn.execute("DELETE FROM concepts WHERE id = ?", (cid,))
    conn.commit()
    if cid not in today_ids and cid in historical_ids:
        return TestResult(
            "phase6_bi_temporal",
            "Phase 6",
            "pass",
            "expired concept excluded today, visible at historical as_of",
        )
    return TestResult(
        "phase6_bi_temporal",
        "Phase 6",
        "fail",
        f"today={cid in today_ids}, historical={cid in historical_ids}",
    )


# ============================================================
# Phase 7 -- recall with outcome_floor
# ============================================================


def test_phase7_recall_respects_outcome_floor(conn: sqlite3.Connection, ws: str) -> TestResult:
    """Recall should NOT return any hit with outcome_score < floor."""
    from agent_memory_lite.repositories.soft_edges_repo import upsert_soft_edge  # noqa: PLC0415

    a, b = "crash_recall_pos", "crash_recall_neg"
    conn.execute("DELETE FROM decisions WHERE id IN (?, ?)", (a, b))
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, ?, 'crashtopic positive', 'b', 'crashtopic positive',
                   'active', ?, ?, ?, 0.6, 0)""",
        (a, ws, iso_now(), iso_now(), iso_now()),
    )
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, ?, 'crashtopic failed approach', 'b',
                   'crashtopic failed approach', 'active', ?, ?, ?, -0.8, 0)""",
        (b, ws, iso_now(), iso_now(), iso_now()),
    )
    upsert_soft_edge(
        conn,
        workspace_id=ws,
        src=f"decision:{a}",
        dst=f"decision:{b}",
        kind="co_retrieved",
        weight_increment=3.0,
    )
    conn.commit()
    hits = recall(conn, workspace_id=ws, topic="crashtopic", outcome_floor=0.0, limit=10)
    hit_ids = {h.object_id for h in hits}
    # Cleanup.
    conn.execute(
        "DELETE FROM soft_edges WHERE workspace_id = ? AND src_qualified_name = ? "
        "AND dst_qualified_name = ?",
        (ws, f"decision:{a}", f"decision:{b}"),
    )
    conn.execute("DELETE FROM decisions WHERE id IN (?, ?)", (a, b))
    conn.commit()
    if a in hit_ids and b not in hit_ids:
        return TestResult(
            "phase7_outcome_floor",
            "Phase 7",
            "pass",
            "positive-outcome hit kept; negative-outcome neighbour filtered",
            {"positive_in": a in hit_ids, "negative_filtered": b not in hit_ids},
        )
    return TestResult(
        "phase7_outcome_floor",
        "Phase 7",
        "fail",
        f"pos_in={a in hit_ids}, neg_in={b in hit_ids}, hits={hit_ids}",
    )


def test_phase7_causal_links_surface_in_recall(conn: sqlite3.Connection, ws: str) -> TestResult:
    """A causal_link on a recall hit should appear in its ``causal_links`` field."""
    new_id, old_id = "crash_causal_new", "crash_causal_old"
    conn.execute("DELETE FROM decisions WHERE id IN (?, ?)", (new_id, old_id))
    conn.execute(
        "DELETE FROM causal_links WHERE src_id IN (?, ?) OR dst_id IN (?, ?)",
        (new_id, old_id, new_id, old_id),
    )
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, ?, 'crashcausal new version', 'b', 'crashcausal new',
                   'active', ?, ?, ?, 0.5, 0)""",
        (new_id, ws, iso_now(), iso_now(), iso_now()),
    )
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, ?, 'crashcausal old version', 'b', 'crashcausal old',
                   'active', ?, ?, ?, -0.2, 0)""",
        (old_id, ws, iso_now(), iso_now(), iso_now()),
    )
    _upsert_causal_link(
        conn,
        workspace_id=ws,
        src_kind="decision",
        src_id=new_id,
        dst_kind="decision",
        dst_id=old_id,
        relation="invalidated",
    )
    conn.commit()
    hits = recall(conn, workspace_id=ws, topic="crashcausal", limit=5)
    seed_hit = next((h for h in hits if h.object_id == new_id), None)
    # Cleanup.
    conn.execute(
        "DELETE FROM causal_links WHERE src_id IN (?, ?) OR dst_id IN (?, ?)",
        (new_id, old_id, new_id, old_id),
    )
    conn.execute("DELETE FROM decisions WHERE id IN (?, ?)", (new_id, old_id))
    conn.commit()
    if seed_hit is None:
        return TestResult(
            "phase7_causal_in_recall",
            "Phase 7",
            "fail",
            f"seed not found in recall hits: {[h.object_id for h in hits]}",
        )
    relations = {link["relation"] for link in seed_hit.causal_links}
    if "invalidated" in relations:
        return TestResult(
            "phase7_causal_in_recall",
            "Phase 7",
            "pass",
            f"causal_links: {sorted(relations)}",
        )
    return TestResult(
        "phase7_causal_in_recall",
        "Phase 7",
        "fail",
        f"no 'invalidated' link surfaced; got {relations}",
    )


# ============================================================
# Cross-cutting -- organ_pass integration + idempotency + brief
# ============================================================


def test_organ_pass_idempotent(conn: sqlite3.Connection, ws: str) -> TestResult:
    """Two back-to-back organ_pass calls — second should write zero new edges."""
    settings = Settings()
    first = run_organ_pass(conn, workspace_id=ws, settings=settings)
    conn.commit()
    second = run_organ_pass(conn, workspace_id=ws, settings=settings)
    conn.commit()
    first_errors = list(first.errors)
    second_errors = list(second.errors)
    second_new_work = (
        second.hebbian_edges_upserted
        + second.insights_promoted
        + second.causal_invalidated
        + second.causal_derived
        + second.reflex_rules_distilled
    )
    if not first_errors and not second_errors and second_new_work == 0:
        return TestResult(
            "organ_pass_idempotent",
            "Integration",
            "pass",
            "second pass = 0 new edges/promotes/links/distills",
            {
                "first_summary": first.to_dict(),
                "second_summary": second.to_dict(),
            },
        )
    return TestResult(
        "organ_pass_idempotent",
        "Integration",
        "fail",
        f"errors first={first_errors} second={second_errors} second_new={second_new_work}",
    )


def test_brief_has_all_eight_sections(conn: sqlite3.Connection, ws: str) -> TestResult:
    """compose_brief returns all 8 expected sections; self-model line present."""
    # Clear cache so we always render fresh.
    from agent_memory_lite.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()
    brief = compose_brief(conn, workspace_id=ws)
    section_names = [s.name for s in brief.sections]
    expected = {
        "identity",
        "behaviors",
        "decisions",
        "state",
        "code_hubs",
        "associates",
        "recent_insights",
        "watch_outs",
    }
    missing = expected - set(section_names)
    if missing:
        return TestResult(
            "brief_sections",
            "Integration",
            "fail",
            f"missing sections: {missing}",
        )
    has_identity_line = "I work on" in brief.body_md or "workspace" in brief.body_md.lower()
    if not has_identity_line:
        return TestResult(
            "brief_sections",
            "Integration",
            "fail",
            "self-model identity line missing from brief",
        )
    return TestResult(
        "brief_sections",
        "Integration",
        "pass",
        f"all 8 sections present, body {len(brief.body_md)} chars",
        {"body_chars": len(brief.body_md), "token_count": brief.token_count},
    )


# ============================================================
# Runner
# ============================================================


_TESTS: list[tuple[str, Callable[[sqlite3.Connection, str], TestResult]]] = [
    ("Phase 1 / archived -> -1.0", test_phase1_archived_drops_to_minus_one),
    ("Phase 1 / superseded -> < 0", test_phase1_superseded_drops_below_zero),
    ("Phase 2 / HeLa-Mem gate blocks", test_phase2_hela_mem_gate_blocks_double_zero),
    ("Phase 2 / one-positive passes gate", test_phase2_one_positive_lets_pair_through),
    ("Phase 3 / high-confidence promotes", test_phase3_promote_high_confidence_insight),
    ("Phase 3 / low-confidence skipped", test_phase3_low_confidence_does_not_promote),
    ("Phase 4 / reflex blocks edit", test_phase4_reflex_blocks_edit_without_impact_check),
    ("Phase 4 / reflex skipped on precondition", test_phase4_reflex_allows_when_precondition_met),
    ("Phase 5 / self-model refresh", test_phase5_self_model_refresh_writes_row),
    ("Phase 6 / bi-temporal as_of", test_phase6_expired_concept_excluded_today),
    ("Phase 7 / outcome_floor filter", test_phase7_recall_respects_outcome_floor),
    ("Phase 7 / causal_links in recall", test_phase7_causal_links_surface_in_recall),
    ("Integration / organ_pass idempotent", test_organ_pass_idempotent),
    ("Integration / brief 8 sections", test_brief_has_all_eight_sections),
]


def run_all(db_path: Path, workspace_id: str) -> CrashReport:
    report = CrashReport(db_path=str(db_path), workspace_id=workspace_id)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for label, fn in _TESTS:
            try:
                result = fn(conn, workspace_id)
            except Exception:
                result = TestResult(
                    name=label.split(" / ")[-1],
                    phase=label.split(" / ")[0],
                    status="fail",
                    detail=traceback.format_exc(),
                )
            report.add(result)
    finally:
        conn.close()
    return report


def render_human(report: CrashReport) -> str:
    lines = [
        f"# Crash test: {report.workspace_id}",
        f"  db: {report.db_path}",
        f"  results: {report.passed} passed, {report.failed} failed, {report.skipped} skipped",
        "",
    ]
    for r in report.results:
        if r.status == "pass":
            icon = f"{GREEN}[OK]{RESET}"
        elif r.status == "fail":
            icon = f"{RED}[FAIL]{RESET}"
        else:
            icon = f"{YELLOW}[SKIP]{RESET}"
        lines.append(f"{icon} {r.phase} / {r.name}")
        if r.detail:
            for line in r.detail.splitlines():
                lines.append(f"      {line}")
        if r.metrics:
            lines.append(f"      metrics: {json.dumps(r.metrics, default=str)}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("--db", required=True, type=Path, help="Sandbox DB path.")
    parser.add_argument("--workspace", required=True, help="Workspace id.")
    parser.add_argument("--json", action="store_true", help="JSON output.")
    args = parser.parse_args()
    if not args.db.exists():
        print(f"db not found: {args.db}", file=sys.stderr)
        return 2
    report = run_all(args.db, args.workspace)
    if args.json:
        print(
            json.dumps(
                {
                    "db": report.db_path,
                    "workspace_id": report.workspace_id,
                    "passed": report.passed,
                    "failed": report.failed,
                    "skipped": report.skipped,
                    "results": [
                        {
                            "name": r.name,
                            "phase": r.phase,
                            "status": r.status,
                            "detail": r.detail,
                            "metrics": r.metrics,
                        }
                        for r in report.results
                    ],
                },
                indent=2,
                default=str,
            )
        )
    else:
        print(render_human(report))
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
