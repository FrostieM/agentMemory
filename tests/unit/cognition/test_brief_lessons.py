"""Phase 3: brief surfaces operator-reviewed 'lesson' insights.

Lessons are promoted to status 'new'/'accepted' and so skip the 'candidate'
status that the Recent insights section filters on -- without a dedicated
section the high-confidence reviewed lessons never surfaced (0 surfaces) while
raw 0.55 consolidation candidates did.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.cognition.brief import compose_brief
from agent_memory_lite.cognition.brief_sections_organ import _build_lessons
from agent_memory_lite.utils.time import iso_now


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


def _seed_lesson(
    conn: sqlite3.Connection,
    *,
    id_: str,
    summary: str,
    status: str = "accepted",
    insight_type: str = "lesson",
    confidence: float = 0.9,
    outcome: float = 0.0,
    updated_at: str | None = None,
) -> None:
    timestamp = updated_at or iso_now()
    conn.execute(
        """INSERT INTO insights
           (id, workspace_id, insight_type, summary, gist, status, confidence,
            outcome_score, created_at, updated_at)
           VALUES (?, 'ws', ?, ?, ?, ?, ?, ?, ?, ?)""",
        # gist = the full summary (real lessons carry a ~120-char gist), so tests
        # can exercise realistic bullet lengths, not artificially short ones.
        (
            id_,
            insight_type,
            summary,
            summary,
            status,
            confidence,
            outcome,
            timestamp,
            timestamp,
        ),
    )
    conn.commit()


# ----- end-to-end wiring (assembly + compose) -----


def test_accepted_lesson_surfaces(conn: sqlite3.Connection) -> None:
    """The gap this closes: a status='accepted' lesson now reaches the brief.
    Fails on main -- there is no Lessons section."""
    _seed_lesson(conn, id_="les_a", summary="kelly beats fixed fraction", status="accepted")
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Lessons learned" in brief.body_md
    assert "les_a" in brief.body_md


def test_new_lesson_surfaces(conn: sqlite3.Connection) -> None:
    _seed_lesson(conn, id_="les_new", summary="warm cache before first call", status="new")
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Lessons learned" in brief.body_md
    assert "les_new" in brief.body_md


def test_full_length_lesson_renders_at_default_budget(conn: sqlite3.Connection) -> None:
    """Realistic guard: a lesson with a full ~120-char gist (as all 9 real
    lessons have) must actually surface its BULLET at the default 500 budget, not
    just a bare header. Guards the budget the lessons section was given (the
    15-token slice it would otherwise get drops every real bullet)."""
    gist = (
        "Warm the embedding model on the main thread before the first stdio call "
        "so the cold-start scan cannot hang the MCP handshake on Windows."
    )
    assert len(gist) >= 120  # representative of the real lesson corpus
    _seed_lesson(conn, id_="les_real", summary=gist, confidence=0.95)
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Lessons learned" in brief.body_md
    assert "les_real" in brief.body_md  # the bullet itself rendered, not just the header


def test_rejected_and_archived_lessons_excluded(conn: sqlite3.Connection) -> None:
    _seed_lesson(conn, id_="les_rej", summary="rejected lesson", status="rejected")
    _seed_lesson(conn, id_="les_arch", summary="archived lesson", status="archived")
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Lessons learned" not in brief.body_md


def test_negative_outcome_lesson_excluded(conn: sqlite3.Connection) -> None:
    _seed_lesson(conn, id_="les_bad", summary="a regressing lesson", outcome=-0.5)
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Lessons learned" not in brief.body_md


def test_candidate_consolidation_not_in_lessons_section(conn: sqlite3.Connection) -> None:
    """A raw consolidation candidate must go to Recent insights, never Lessons --
    proves the two surfaces stay separate so a reviewed lesson is not diluted by
    raw candidates (and vice versa)."""
    _seed_lesson(
        conn,
        id_="cand_x",
        summary="raw candidate signal observed",
        insight_type="consolidation",
        status="candidate",
    )
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Lessons learned" not in brief.body_md
    assert "## Recent insights" in brief.body_md
    assert "cand_x" in brief.body_md


def test_low_signal_lesson_filtered(conn: sqlite3.Connection) -> None:
    _seed_lesson(conn, id_="les_noise", summary="file_indexed src/foo.py")
    brief = compose_brief(conn, workspace_id="ws")
    assert "## Lessons learned" not in brief.body_md


def test_lesson_surfacing_stamps_surface_count(conn: sqlite3.Connection) -> None:
    _seed_lesson(conn, id_="les_stamp", summary="surface me once")
    compose_brief(conn, workspace_id="ws")
    row = conn.execute(
        "SELECT last_surfaced_at, surface_count FROM insights WHERE id = 'les_stamp'"
    ).fetchone()
    assert row is not None
    assert row["last_surfaced_at"] is not None
    assert row["surface_count"] == 1


# ----- builder-level cap + ordering (generous budget, no interference) -----


def test_lessons_capped_at_limit(conn: sqlite3.Connection) -> None:
    for i in range(5):
        _seed_lesson(conn, id_=f"les_{i}", summary=f"lesson number {i}", confidence=0.95 - i * 0.01)
    section = _build_lessons(conn, "ws", budget=400, limit=2)
    rendered = [ln for ln in section.lines if ln.startswith("- ")]
    assert len(rendered) == 2  # flood guard, even with budget to spare


def test_higher_confidence_lesson_ranks_first(conn: sqlite3.Connection) -> None:
    _seed_lesson(conn, id_="les_low", summary="lower confidence lesson", confidence=0.85)
    _seed_lesson(conn, id_="les_high", summary="higher confidence lesson", confidence=0.95)
    section = _build_lessons(conn, "ws", budget=400, limit=1)
    body = "\n".join(section.lines)
    assert "les_high" in body
    assert "les_low" not in body


def test_lone_header_suppressed_when_no_bullet_fits(conn: sqlite3.Connection) -> None:
    """When the budget fits the header but no bullet, render NOTHING -- a bare
    '## Lessons learned' is noise and the freed budget goes to recipients."""
    _seed_lesson(conn, id_="les_x", summary="a lesson whose bullet will not fit a tiny budget")
    section = _build_lessons(conn, "ws", budget=4, limit=1)  # ~fits the 3-token header only
    assert section.lines == []
