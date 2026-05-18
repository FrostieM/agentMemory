"""Phase 1: lint._prior_failures surfaces low-outcome decisions/behaviors."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.cognition.lint import _prior_failures
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


def _seed_decision(
    conn: sqlite3.Connection, *, id_: str, title: str, gist: str, outcome: float = 0.0
) -> None:
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, gist, status, valid_from,
            created_at, updated_at, outcome_score, pinned)
           VALUES (?, 'ws', ?, 'body', ?, 'active', ?, ?, ?, ?, 0)""",
        (id_, title, gist, iso_now(), iso_now(), iso_now(), outcome),
    )
    conn.commit()


def _seed_behavior(
    conn: sqlite3.Connection, *, id_: str, name: str, rule_one_line: str, outcome: float = 0.0
) -> None:
    conn.execute(
        """INSERT INTO behaviors
           (id, workspace_id, name, kind, scope, priority, rule, rule_one_line,
            applies_to_json, active, pinned, created_at, updated_at, outcome_score)
           VALUES (?, 'ws', ?, 'operating_rule', 'workspace', 'project_convention',
                   'rule body', ?, '[]', 1, 0, ?, ?, ?)""",
        (id_, name, rule_one_line, iso_now(), iso_now(), outcome),
    )
    conn.commit()


def test_low_outcome_decision_surfaces_as_failure(conn: sqlite3.Connection) -> None:
    _seed_decision(
        conn, id_="dec_kelly", title="Adopt Kelly", gist="full Kelly sizing", outcome=-0.6
    )
    failures = _prior_failures(conn, "ws", "kelly")
    kinds = [f["kind"] for f in failures]
    assert "low_outcome_decision" in kinds
    failure = next(f for f in failures if f["kind"] == "low_outcome_decision")
    assert failure["id"] == "dec_kelly"
    assert failure["outcome_score"] < 0


def test_positive_outcome_decision_does_not_surface(conn: sqlite3.Connection) -> None:
    _seed_decision(
        conn, id_="dec_kelly", title="Adopt Kelly", gist="quarter Kelly sizing", outcome=0.5
    )
    failures = _prior_failures(conn, "ws", "kelly")
    assert failures == []


def test_low_outcome_behavior_surfaces(conn: sqlite3.Connection) -> None:
    _seed_behavior(
        conn, id_="beh_x", name="kelly-cap", rule_one_line="Cap Kelly at quarter", outcome=-0.5
    )
    failures = _prior_failures(conn, "ws", "kelly")
    kinds = [f["kind"] for f in failures]
    assert "low_outcome_behavior" in kinds


def test_threshold_minus_zero_three_is_floor(conn: sqlite3.Connection) -> None:
    """Decisions at outcome=-0.2 must NOT surface (threshold is -0.3)."""
    _seed_decision(conn, id_="dec_borderline", title="Adopt Kelly", gist="x", outcome=-0.2)
    failures = _prior_failures(conn, "ws", "kelly")
    assert not any(f["id"] == "dec_borderline" for f in failures)


def _seed_insight(
    conn: sqlite3.Connection,
    *,
    id_: str,
    summary: str,
    insight_type: str = "consolidation",
    outcome: float = 0.0,
) -> None:
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, status, confidence,
            outcome_score, created_at, updated_at)
           VALUES (?, 'ws', ?, ?, ?, 'candidate', 0.6, ?, ?, ?)""",
        (id_, insight_type, summary, summary[:60], outcome, iso_now(), iso_now()),
    )
    conn.commit()


def test_low_outcome_insight_surfaces_as_failure(conn: sqlite3.Connection) -> None:
    """Phase 3: insights with insight_type contradiction/consolidation and
    outcome_score below threshold surface as low_outcome_insight failures."""
    _seed_insight(
        conn,
        id_="ins_kelly_bad",
        summary="kelly sizing pattern contradicted by recent divergence",
        insight_type="contradiction",
        outcome=-0.5,
    )
    failures = _prior_failures(conn, "ws", "kelly")
    kinds = [f["kind"] for f in failures]
    assert "low_outcome_insight" in kinds


def test_handles_pre_migration_db_gracefully() -> None:
    """When outcome_score column does not exist, _prior_failures must not raise."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    # Do NOT apply 0002. _prior_failures should still return rejected theories
    # and skip the outcome queries silently.
    c.execute(
        """INSERT INTO theories
           (id, workspace_id, title, claim, status, created_at, updated_at)
           VALUES ('th_x', 'ws', 'Kelly safe', 'Half-Kelly is safe', 'rejected',
                   ?, ?)""",
        (iso_now(), iso_now()),
    )
    c.commit()
    failures = _prior_failures(c, "ws", "kelly")
    # The rejected theory still surfaces; the outcome queries no-op.
    assert any(f["kind"] == "rejected_theory" for f in failures)
    c.close()
