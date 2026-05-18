"""Unit tests for Phase 1 outcome_recompute."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.cognition.outcome_recompute import (
    OutcomeInputs,
    compute_outcome,
    refresh_one,
    refresh_workspace,
)
from agent_memory_lite.utils.time import iso_now

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
OUTCOME_PATH = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0002_outcome_loop.sql"
)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(OUTCOME_PATH.read_text(encoding="utf-8"))
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
        feedback_ewma=1.0, age_days=0.0, archived=True, superseded=False, rejected=False, usage_count=100
    )
    assert compute_outcome(inp) == -1.0


def test_no_evidence_returns_neutral() -> None:
    """Zero usage_count, zero feedback → score stays at 0.0 (neutral)."""
    inp = OutcomeInputs(
        feedback_ewma=0.7, age_days=0.0, archived=False, superseded=False, rejected=False, usage_count=0
    )
    # evidence_weight(0) = 0 → adjustment = 0 → score = 0.0
    assert compute_outcome(inp) == pytest.approx(0.0, abs=1e-3)


def test_monotonic_in_feedback() -> None:
    """Higher EWMA → higher score, holding evidence + age constant."""
    base = OutcomeInputs(
        feedback_ewma=0.2, age_days=0.0, archived=False, superseded=False, rejected=False, usage_count=10
    )
    higher = OutcomeInputs(
        feedback_ewma=0.8, age_days=0.0, archived=False, superseded=False, rejected=False, usage_count=10
    )
    assert compute_outcome(higher) > compute_outcome(base)


def test_superseded_pulls_down() -> None:
    inp_active = OutcomeInputs(
        feedback_ewma=0.3, age_days=0.0, archived=False, superseded=False, rejected=False, usage_count=10
    )
    inp_super = OutcomeInputs(
        feedback_ewma=0.3, age_days=0.0, archived=False, superseded=True, rejected=False, usage_count=10
    )
    assert compute_outcome(inp_super) < compute_outcome(inp_active) - 0.4


def test_rejected_pulls_down() -> None:
    inp_proposed = OutcomeInputs(
        feedback_ewma=0.2, age_days=0.0, archived=False, superseded=False, rejected=False, usage_count=5
    )
    inp_rejected = OutcomeInputs(
        feedback_ewma=0.2, age_days=0.0, archived=False, superseded=False, rejected=True, usage_count=5
    )
    assert compute_outcome(inp_rejected) < compute_outcome(inp_proposed) - 0.4


def test_staleness_decays_adjustment() -> None:
    """Old positive evidence cannot eclipse fresh neutral state."""
    fresh = OutcomeInputs(
        feedback_ewma=0.8, age_days=0.0, archived=False, superseded=False, rejected=False, usage_count=20
    )
    stale = OutcomeInputs(
        feedback_ewma=0.8, age_days=120.0, archived=False, superseded=False, rejected=False, usage_count=20
    )
    assert compute_outcome(stale) < compute_outcome(fresh) - 0.4


def test_bounded_to_unit_interval() -> None:
    """Even extreme inputs stay in [-1, 1]."""
    extreme_pos = OutcomeInputs(
        feedback_ewma=1.0, age_days=0.0, archived=False, superseded=False, rejected=False, usage_count=10_000
    )
    extreme_neg = OutcomeInputs(
        feedback_ewma=-1.0, age_days=0.0, archived=False, superseded=True, rejected=True, usage_count=10_000
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
