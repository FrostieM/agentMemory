"""Unit tests for Phase 1 outcome_recompute."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.cognition.outcome_recompute import (
    OutcomeInputs,
    compute_outcome,
    refresh_one,
    refresh_workspace,
)
from agent_memory_lite.utils.time import iso_now


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


# ============================================================
# compute_outcome — pure math
# ============================================================


def test_archived_pins_to_negative_one() -> None:
    """An archived row always returns -1.0 regardless of feedback."""
    inp = OutcomeInputs(
        feedback_ewma=1.0,
        age_days=0.0,
        archived=True,
        superseded=False,
        rejected=False,
        usage_count=100,
    )
    assert compute_outcome(inp) == -1.0


def test_no_evidence_returns_neutral() -> None:
    """Zero usage_count, zero feedback → score stays at 0.0 (neutral)."""
    inp = OutcomeInputs(
        feedback_ewma=0.7,
        age_days=0.0,
        archived=False,
        superseded=False,
        rejected=False,
        usage_count=0,
    )
    # evidence_weight(0) = 0 → adjustment = 0 → score = 0.0
    assert compute_outcome(inp) == pytest.approx(0.0, abs=1e-3)


def test_monotonic_in_feedback() -> None:
    """Higher EWMA → higher score, holding evidence + age constant."""
    base = OutcomeInputs(
        feedback_ewma=0.2,
        age_days=0.0,
        archived=False,
        superseded=False,
        rejected=False,
        usage_count=10,
    )
    higher = OutcomeInputs(
        feedback_ewma=0.8,
        age_days=0.0,
        archived=False,
        superseded=False,
        rejected=False,
        usage_count=10,
    )
    assert compute_outcome(higher) > compute_outcome(base)


def test_superseded_pulls_down() -> None:
    inp_active = OutcomeInputs(
        feedback_ewma=0.3,
        age_days=0.0,
        archived=False,
        superseded=False,
        rejected=False,
        usage_count=10,
    )
    inp_super = OutcomeInputs(
        feedback_ewma=0.3,
        age_days=0.0,
        archived=False,
        superseded=True,
        rejected=False,
        usage_count=10,
    )
    assert compute_outcome(inp_super) < compute_outcome(inp_active) - 0.4


def test_rejected_pulls_down() -> None:
    inp_proposed = OutcomeInputs(
        feedback_ewma=0.2,
        age_days=0.0,
        archived=False,
        superseded=False,
        rejected=False,
        usage_count=5,
    )
    inp_rejected = OutcomeInputs(
        feedback_ewma=0.2,
        age_days=0.0,
        archived=False,
        superseded=False,
        rejected=True,
        usage_count=5,
    )
    assert compute_outcome(inp_rejected) < compute_outcome(inp_proposed) - 0.4


def test_staleness_decays_adjustment() -> None:
    """Old positive evidence cannot eclipse fresh neutral state."""
    fresh = OutcomeInputs(
        feedback_ewma=0.8,
        age_days=0.0,
        archived=False,
        superseded=False,
        rejected=False,
        usage_count=20,
    )
    stale = OutcomeInputs(
        feedback_ewma=0.8,
        age_days=120.0,
        archived=False,
        superseded=False,
        rejected=False,
        usage_count=20,
    )
    assert compute_outcome(stale) < compute_outcome(fresh) - 0.4


def test_bounded_to_unit_interval() -> None:
    """Even extreme inputs stay in [-1, 1]."""
    extreme_pos = OutcomeInputs(
        feedback_ewma=1.0,
        age_days=0.0,
        archived=False,
        superseded=False,
        rejected=False,
        usage_count=10_000,
    )
    extreme_neg = OutcomeInputs(
        feedback_ewma=-1.0,
        age_days=0.0,
        archived=False,
        superseded=True,
        rejected=True,
        usage_count=10_000,
    )
    pos = compute_outcome(extreme_pos)
    neg = compute_outcome(extreme_neg)
    assert -1.0 <= neg <= 1.0
    assert -1.0 <= pos <= 1.0


# ============================================================
# refresh_one + refresh_workspace — round-trip with SQLite
# ============================================================


def _seed_decision(conn: sqlite3.Connection, **kwargs: object) -> None:
    defaults: dict[str, object] = {
        "id": "dec_x",
        "workspace_id": "ws",
        "title": "T",
        "decision_text": "body",
        "status": "active",
        "valid_from": iso_now(),
        "created_at": iso_now(),
        "updated_at": iso_now(),
        "feedback_ewma": 0.0,
        "outcome_score": 0.0,
        "pinned": 0,
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    qs = ", ".join("?" for _ in defaults)
    conn.execute(f"INSERT INTO decisions ({cols}) VALUES ({qs})", tuple(defaults.values()))
    conn.commit()


def test_refresh_one_archived_returns_minus_one(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id="dec_1", status="archived", feedback_ewma=0.5)
    score = refresh_one(conn, kind="decision", object_id="dec_1", now_iso=iso_now())
    assert score == -1.0
    row = conn.execute("SELECT outcome_score FROM decisions WHERE id='dec_1'").fetchone()
    assert row[0] == -1.0


def test_refresh_one_rejected_decision_scores_negative(conn: sqlite3.Connection) -> None:
    """A rejected decision is pinned hard-negative -- mirrors _theory_inputs and
    the module docstring ('archived / rejected / superseded yields a hard
    negative'). Before the fix _decision_inputs hardcoded rejected=False, so a
    rejected decision's outcome was driven only by feedback (stayed ~0)."""
    _seed_decision(conn, id="dec_rej", status="rejected", feedback_ewma=0.3)
    score = refresh_one(conn, kind="decision", object_id="dec_rej", now_iso=iso_now())
    assert score is not None
    assert score < 0.0, f"rejected decision should be pinned negative; got {score}"
    row = conn.execute("SELECT outcome_score FROM decisions WHERE id='dec_rej'").fetchone()
    assert row[0] < 0.0


def test_new_decision_with_supersedes_pointer_is_not_treated_as_superseded(
    conn: sqlite3.Connection,
) -> None:
    """Regression: a decision that SUPERSEDES another (has supersedes_decision_id
    pointing at some older id) is the NEW row, not the superseded one. Earlier
    code treated ``bool(supersedes_decision_id)`` as the superseded signal,
    which inverted the semantic and dropped every winner's outcome by ~0.5.
    The only correct signal is ``status == 'superseded'``."""
    # OLD decision -- exists as parent.
    _seed_decision(conn, id="dec_old", status="superseded", feedback_ewma=0.3)
    # NEW decision -- supersedes the old; itself active.
    _seed_decision(
        conn,
        id="dec_new",
        status="active",
        feedback_ewma=0.0,
        supersedes_decision_id="dec_old",
    )
    score_new = refresh_one(conn, kind="decision", object_id="dec_new", now_iso=iso_now())
    # The NEW row should NOT carry the SUPERSEDED_PENALTY. With
    # feedback_ewma=0 and no penalty, the score should hover at 0,
    # certainly not be pulled deep negative.
    assert score_new is not None
    assert score_new >= -0.1, (
        f"NEW decision wrongly penalised (semantic inversion of supersedes_decision_id); "
        f"got {score_new}"
    )
    # The OLD row -- status='superseded' -- DOES carry the penalty.
    score_old = refresh_one(conn, kind="decision", object_id="dec_old", now_iso=iso_now())
    assert score_old is not None
    assert score_old < 0.0, f"OLD (superseded-status) decision should be negative; got {score_old}"


def test_refresh_one_missing_returns_none(conn: sqlite3.Connection) -> None:
    assert refresh_one(conn, kind="decision", object_id="missing", now_iso=iso_now()) is None


def test_refresh_one_unknown_kind_returns_none(conn: sqlite3.Connection) -> None:
    assert refresh_one(conn, kind="not_a_kind", object_id="x", now_iso=iso_now()) is None


def test_refresh_workspace_is_idempotent(conn: sqlite3.Connection) -> None:
    """Second pass with unchanged data writes zero rows."""
    _seed_decision(conn, id="dec_1", feedback_ewma=0.3)
    first = refresh_workspace(conn, workspace_id="ws", now_iso=iso_now())
    second = refresh_workspace(conn, workspace_id="ws", now_iso=iso_now())
    assert first["decision"] >= 0
    # Idempotent: nothing changed between passes.
    assert second["decision"] == 0


def test_refresh_workspace_other_workspaces_untouched(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id="dec_a", workspace_id="ws_a", feedback_ewma=0.5)
    _seed_decision(conn, id="dec_b", workspace_id="ws_b", feedback_ewma=0.5)
    refresh_workspace(conn, workspace_id="ws_a", now_iso=iso_now())
    a = conn.execute("SELECT outcome_score FROM decisions WHERE id='dec_a'").fetchone()[0]
    b = conn.execute("SELECT outcome_score FROM decisions WHERE id='dec_b'").fetchone()[0]
    assert a != 0.0 or b == 0.0  # ws_a touched, ws_b NOT.
    assert b == 0.0  # ws_b never refreshed


# ============================================================
# decision evidence weight from usage-feedback samples
# (closes the gap where decisions were pinned at outcome 0 because usage_count
# was hardcoded 0 -> evidence_weight(0)=0 zeroed any feedback)
# ============================================================


def _seed_feedback(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    usefulness: float,
    source: str = "agent_observed",
    workspace_id: str = "ws",
    source_type: str = "decision",
    n: int = 1,
) -> None:
    for i in range(n):
        conn.execute(
            "INSERT INTO memory_usage_feedback "
            "(id, workspace_id, source_type, source_id, query, usefulness, notes, created_at, source) "
            "VALUES (?, ?, ?, ?, '', ?, '', ?, ?)",
            (f"fb_{source_id}_{source}_{i}", workspace_id, source_type, source_id, usefulness,
             iso_now(), source),
        )
    conn.commit()


def test_decision_with_feedback_earns_nonzero_outcome(conn: sqlite3.Connection) -> None:
    """A decision with positive feedback_ewma AND >=1 usage-feedback sample now
    earns a positive outcome_score -- before, usage_count=0 pinned it at 0."""
    _seed_decision(conn, id="dec_fb", feedback_ewma=0.7, last_retrieved_at=iso_now())
    _seed_feedback(conn, source_id="dec_fb", usefulness=0.7)
    refresh_workspace(conn, workspace_id="ws", now_iso=iso_now())
    score = conn.execute("SELECT outcome_score FROM decisions WHERE id='dec_fb'").fetchone()[0]
    assert score > 0.0


def test_decision_without_feedback_stays_zero(conn: sqlite3.Connection) -> None:
    """No usage-feedback sample -> evidence_weight(0)=0 -> stays 0 even with a
    non-zero feedback_ewma column. Confabulation guard: feedback with no evidence
    cannot move the score."""
    _seed_decision(conn, id="dec_nofb", feedback_ewma=0.7, last_retrieved_at=iso_now())
    refresh_workspace(conn, workspace_id="ws", now_iso=iso_now())
    score = conn.execute("SELECT outcome_score FROM decisions WHERE id='dec_nofb'").fetchone()[0]
    assert score == 0.0


def test_self_loop_feedback_is_not_evidence(conn: sqlite3.Connection) -> None:
    """self_loop feedback is excluded from the evidence count (mirrors the EWMA
    aggregator), so it cannot grant a decision evidence weight."""
    _seed_decision(conn, id="dec_sl", feedback_ewma=0.7, last_retrieved_at=iso_now())
    _seed_feedback(conn, source_id="dec_sl", usefulness=0.7, source="self_loop")
    refresh_workspace(conn, workspace_id="ws", now_iso=iso_now())
    score = conn.execute("SELECT outcome_score FROM decisions WHERE id='dec_sl'").fetchone()[0]
    assert score == 0.0


def test_more_feedback_samples_give_more_evidence_weight(conn: sqlite3.Connection) -> None:
    """evidence_weight is monotonic in sample count, so more usage feedback ->
    a stronger (still sub-linear) outcome for the same feedback_ewma."""
    _seed_decision(conn, id="dec_one", feedback_ewma=0.7, last_retrieved_at=iso_now())
    _seed_decision(conn, id="dec_many", feedback_ewma=0.7, last_retrieved_at=iso_now())
    _seed_feedback(conn, source_id="dec_one", usefulness=0.7, n=1)
    _seed_feedback(conn, source_id="dec_many", usefulness=0.7, n=5)
    refresh_workspace(conn, workspace_id="ws", now_iso=iso_now())
    one = conn.execute("SELECT outcome_score FROM decisions WHERE id='dec_one'").fetchone()[0]
    many = conn.execute("SELECT outcome_score FROM decisions WHERE id='dec_many'").fetchone()[0]
    assert 0.0 < one < many


def test_negative_feedback_decision_scores_negative(conn: sqlite3.Connection) -> None:
    """A decision with negative feedback now earns a NEGATIVE outcome_score, which
    crosses the brief's outcome<0 filter and evicts it from 'Active decisions'."""
    _seed_decision(conn, id="dec_neg", feedback_ewma=-0.8, last_retrieved_at=iso_now())
    _seed_feedback(conn, source_id="dec_neg", usefulness=-0.8)
    refresh_workspace(conn, workspace_id="ws", now_iso=iso_now())
    score = conn.execute("SELECT outcome_score FROM decisions WHERE id='dec_neg'").fetchone()[0]
    assert score < 0.0


def test_decision_feedback_count_capped_per_day(conn: sqlite3.Connection) -> None:
    """A same-day feedback flood is capped to the EWMA's per-(source, day) basis,
    so the evidence weight cannot exceed what the (capped) feedback_ewma is built
    from: 10 vs 30 same-day samples yield the SAME outcome."""
    ts = iso_now()  # shared so the two rows have identical staleness (isolate the cap)
    _seed_decision(conn, id="dec_cap", feedback_ewma=0.7, last_retrieved_at=ts)
    _seed_decision(conn, id="dec_capx", feedback_ewma=0.7, last_retrieved_at=ts)
    _seed_feedback(conn, source_id="dec_cap", usefulness=0.7, n=10)  # exactly the default cap
    _seed_feedback(conn, source_id="dec_capx", usefulness=0.7, n=30)  # well over the cap
    refresh_workspace(conn, workspace_id="ws", now_iso=iso_now())  # default cap = 10
    a = conn.execute("SELECT outcome_score FROM decisions WHERE id='dec_cap'").fetchone()[0]
    b = conn.execute("SELECT outcome_score FROM decisions WHERE id='dec_capx'").fetchone()[0]
    assert a == b  # both capped to 10 samples -> identical evidence weight
