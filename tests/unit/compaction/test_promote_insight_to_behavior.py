"""Phase 3: promote_insight_to_behavior tests.

The gate is conservative on purpose: a low-confidence or single-surface
insight never crosses; the same insight on a second pass is a no-op."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.compaction.promote_insight_to_behavior import (
    MIN_CONFIDENCE,
    MIN_SURFACE_EVENTS,
    promote_eligible_insights,
)
from agent_memory_lite.utils.time import iso_now

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
OUTCOME_PATH = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0002_outcome_loop.sql"
)
HEBBIAN_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0003_hebbian.sql"
CONSOL_PATH = (
    Path(__file__).resolve().parents[3]
    / "migrations"
    / "canonical"
    / "0004_consolidation_feedback.sql"
)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(OUTCOME_PATH.read_text(encoding="utf-8"))
    c.executescript(HEBBIAN_PATH.read_text(encoding="utf-8"))
    c.executescript(CONSOL_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_insight(
    conn: sqlite3.Connection,
    *,
    id_: str,
    confidence: float,
    surface_count: int,
    status: str = "candidate",
    insight_type: str = "consolidation",
    summary: str = "auto-promote candidate insight body",
) -> None:
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, status,
            confidence, surface_count, created_at, updated_at)
           VALUES (?, 'ws', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            id_,
            insight_type,
            summary,
            summary[:80],
            status,
            confidence,
            surface_count,
            iso_now(),
            iso_now(),
        ),
    )
    conn.commit()


# ============================================================
# gate behaviour
# ============================================================


def test_high_confidence_two_surface_promotes(conn: sqlite3.Connection) -> None:
    _seed_insight(conn, id_="ins_promote", confidence=0.8, surface_count=2)
    stats = promote_eligible_insights(conn, workspace_id="ws")
    assert stats.promoted == 1
    assert stats.skipped == 0
    row = conn.execute(
        "SELECT pinned, source_type, source_id FROM behaviors WHERE source_id = 'ins_promote'"
    ).fetchone()
    assert row is not None
    assert row["pinned"] == 1
    assert row["source_type"] == "insight"


def test_promoted_insight_status_becomes_promoted(conn: sqlite3.Connection) -> None:
    _seed_insight(conn, id_="ins_x", confidence=0.8, surface_count=3)
    promote_eligible_insights(conn, workspace_id="ws")
    status = conn.execute("SELECT status FROM insights WHERE id = 'ins_x'").fetchone()[0]
    assert status == "promoted"


def test_low_confidence_does_not_promote(conn: sqlite3.Connection) -> None:
    _seed_insight(conn, id_="ins_low", confidence=0.5, surface_count=5)
    stats = promote_eligible_insights(conn, workspace_id="ws")
    assert stats.promoted == 0


def test_single_surface_does_not_promote(conn: sqlite3.Connection) -> None:
    _seed_insight(conn, id_="ins_one", confidence=0.9, surface_count=1)
    stats = promote_eligible_insights(conn, workspace_id="ws")
    assert stats.promoted == 0


# ============================================================
# idempotency
# ============================================================


def test_second_pass_no_double_promote(conn: sqlite3.Connection) -> None:
    _seed_insight(conn, id_="ins_dup", confidence=0.8, surface_count=3)
    first = promote_eligible_insights(conn, workspace_id="ws")
    second = promote_eligible_insights(conn, workspace_id="ws")
    assert first.promoted == 1
    assert second.promoted == 0  # status='promoted' filter excludes it


def test_existing_behavior_for_insight_is_skipped(conn: sqlite3.Connection) -> None:
    """If a behavior already references this insight_id, do nothing."""
    _seed_insight(conn, id_="ins_seen", confidence=0.8, surface_count=2, status="candidate")
    # Insert a pre-existing behavior with source_type='insight', source_id=ins_seen.
    conn.execute(
        """INSERT INTO behaviors
           (id, workspace_id, name, kind, scope, priority, rule, rule_one_line,
            applies_to_json, active, pinned, source_type, source_id,
            created_at, updated_at)
           VALUES ('beh_x', 'ws', 'pre-existing', 'operating_rule', 'workspace',
                   'project_convention', 'body', 'one', '[]', 1, 1,
                   'insight', 'ins_seen', ?, ?)""",
        (iso_now(), iso_now()),
    )
    conn.commit()
    stats = promote_eligible_insights(conn, workspace_id="ws")
    assert stats.promoted == 0
    assert stats.skipped == 1


# ============================================================
# constants exposed
# ============================================================


def test_thresholds_are_conservative() -> None:
    """Plan-mandated defaults; protect against silent regression."""
    assert MIN_CONFIDENCE >= 0.7
    assert MIN_SURFACE_EVENTS >= 2
