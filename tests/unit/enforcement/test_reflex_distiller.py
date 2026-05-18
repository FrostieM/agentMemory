"""Phase 4: reflex_distiller tests.

The distiller is conservative on purpose: under ``min_support=3`` a
single low-outcome insight must NOT produce a rule. Three matching
insights produce one advisory rule (never block). Re-running on a drained
corpus is idempotent.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.enforcement.reflex_distiller import distill_workspace
from agent_memory_lite.utils.time import iso_now

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
OUTCOME_PATH = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0002_outcome_loop.sql"
)
REFLEX_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0005_reflexes.sql"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(OUTCOME_PATH.read_text(encoding="utf-8"))
    c.executescript(REFLEX_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_low_outcome_insight(conn: sqlite3.Connection, *, id_: str, summary: str) -> None:
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, status, confidence,
            outcome_score, created_at, updated_at)
           VALUES (?, 'ws', 'contradiction', ?, ?, 'candidate', 0.6, -0.5, ?, ?)""",
        (id_, summary, summary[:60], iso_now(), iso_now()),
    )
    conn.commit()


def test_three_supporting_insights_produce_one_rule(conn: sqlite3.Connection) -> None:
    """min_support=3 is the floor; exactly 3 supports → 1 distilled rule."""
    for i in range(3):
        _seed_low_outcome_insight(
            conn,
            id_=f"ins_{i}",
            summary="edited src/strategy.py without impact_check, broke 4 callers",
        )
    report = distill_workspace(conn, workspace_id="ws", min_support=3)
    assert report.rules_upserted == 1
    row = conn.execute(
        "SELECT trigger_tool, precondition_kind, enforcement FROM reflex_rules"
    ).fetchone()
    assert row["trigger_tool"] == "Edit"
    assert row["precondition_kind"] == "impact_check_within_seconds"
    assert row["enforcement"] == "advisory"  # never auto-block


def test_two_supporting_insights_below_floor(conn: sqlite3.Connection) -> None:
    """min_support=3 means 2 supports → no rule (single-trial trauma guard)."""
    for i in range(2):
        _seed_low_outcome_insight(
            conn,
            id_=f"ins_{i}",
            summary="edited file without impact_check before reading",
        )
    report = distill_workspace(conn, workspace_id="ws", min_support=3)
    assert report.rules_upserted == 0
    rows = conn.execute("SELECT 1 FROM reflex_rules").fetchall()
    assert rows == []


def test_idempotent_second_run(conn: sqlite3.Connection) -> None:
    """Running twice on the same corpus does not duplicate the rule."""
    for i in range(3):
        _seed_low_outcome_insight(conn, id_=f"ins_{i}", summary="edited file without impact_check")
    first = distill_workspace(conn, workspace_id="ws", min_support=3)
    second = distill_workspace(conn, workspace_id="ws", min_support=3)
    assert first.rules_upserted == 1
    assert second.rules_upserted == 0  # dedup gate skips existing rule
    count = conn.execute("SELECT COUNT(*) FROM reflex_rules").fetchone()[0]
    assert count == 1


def test_distiller_picks_up_decision_pattern(conn: sqlite3.Connection) -> None:
    for i in range(3):
        _seed_low_outcome_insight(
            conn,
            id_=f"ins_{i}",
            summary="wrote decision dec_x without search beforehand",
        )
    distill_workspace(conn, workspace_id="ws", min_support=3)
    row = conn.execute("SELECT trigger_tool, precondition_kind FROM reflex_rules").fetchone()
    assert row is not None
    assert row["trigger_tool"] == "memory_write"
    assert row["precondition_kind"] == "memory_search_within_seconds"


def test_distiller_picks_up_deploy_pattern(conn: sqlite3.Connection) -> None:
    for i in range(3):
        _seed_low_outcome_insight(
            conn, id_=f"ins_{i}", summary="deploy step run without playbook fetch"
        )
    distill_workspace(conn, workspace_id="ws", min_support=3)
    row = conn.execute("SELECT trigger_tool, precondition_kind FROM reflex_rules").fetchone()
    assert row is not None
    assert row["trigger_tool"] == "Bash"
    assert row["precondition_kind"] == "playbook_fetch"


def test_positive_outcome_insight_does_not_distill(conn: sqlite3.Connection) -> None:
    """Only outcome < -0.3 insights drive distillation."""
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, status, confidence,
            outcome_score, created_at, updated_at)
           VALUES ('ins_pos', 'ws', 'contradiction', 'edited file without impact_check',
                   'g', 'candidate', 0.7, 0.3, ?, ?)""",
        (iso_now(), iso_now()),
    )
    conn.commit()
    report = distill_workspace(conn, workspace_id="ws", min_support=3)
    assert report.rules_upserted == 0


def test_pre_migration_db_returns_empty() -> None:
    """No reflex_rules table → no work, no error."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(OUTCOME_PATH.read_text(encoding="utf-8"))
    # Do NOT apply 0005.
    report = distill_workspace(c, workspace_id="ws", min_support=3)
    assert report.rules_upserted == 0
    c.close()
