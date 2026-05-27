"""Phase 3: brief surfaces recent insight candidates."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.cognition.brief import compose_brief
from agent_memory_lite.utils.time import iso_now

CONSOL_PATH = (
    Path(__file__).resolve().parents[3]
    / "migrations"
    / "canonical"
    / "0004_consolidation_feedback.sql"
)


@pytest.fixture(autouse=True)
def _isolate_brief_cache() -> Iterator[None]:
    from agent_memory_lite.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()
    yield
    brief_mod._BRIEF_CACHE.clear()


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


def _seed_insight(
    conn: sqlite3.Connection,
    *,
    id_: str,
    summary: str,
    status: str = "candidate",
    insight_type: str = "consolidation",
    outcome: float = 0.0,
) -> None:
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, status, confidence,
            outcome_score, created_at, updated_at)
           VALUES (?, 'ws', ?, ?, ?, ?, 0.6, ?, ?, ?)""",
        (id_, insight_type, summary, summary[:60], status, outcome, iso_now(), iso_now()),
    )
    conn.commit()


def test_recent_insights_section_appears(conn: sqlite3.Connection) -> None:
    _seed_insight(conn, id_="ins_a", summary="kelly sizing pattern observed in last 24h")
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Recent insights" in brief.body_md
    assert "ins_a" in brief.body_md


def test_section_absent_with_no_candidates(conn: sqlite3.Connection) -> None:
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Recent insights" not in brief.body_md


def test_section_excludes_promoted_status(conn: sqlite3.Connection) -> None:
    _seed_insight(conn, id_="ins_done", summary="already promoted", status="promoted")
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Recent insights" not in brief.body_md


def test_section_excludes_negative_outcome(conn: sqlite3.Connection) -> None:
    _seed_insight(conn, id_="ins_bad", summary="negative outcome", outcome=-0.5)
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Recent insights" not in brief.body_md


def test_surfacing_stamps_last_surfaced_at(conn: sqlite3.Connection) -> None:
    _seed_insight(conn, id_="ins_stamp", summary="get me surfaced")
    compose_brief(conn, workspace_id="ws")
    row = conn.execute(
        "SELECT last_surfaced_at, surface_count FROM insights WHERE id = 'ins_stamp'"
    ).fetchone()
    assert row is not None
    assert row["last_surfaced_at"] is not None
    assert row["surface_count"] == 1


def test_surface_count_increments_across_calls(conn: sqlite3.Connection) -> None:
    _seed_insight(conn, id_="ins_tick", summary="surface me twice")
    # First brief stamps surface_count=1. Cache invalidates because the
    # surface-count update bumps updated_at indirectly via brief's
    # fingerprint reset; we explicitly clear the cache between calls.
    compose_brief(conn, workspace_id="ws")
    from agent_memory_lite.cognition import brief as brief_mod  # noqa: PLC0415

    brief_mod._BRIEF_CACHE.clear()
    compose_brief(conn, workspace_id="ws")
    sc = conn.execute("SELECT surface_count FROM insights WHERE id = 'ins_tick'").fetchone()[0]
    assert sc == 2
