"""Phase 1: brief filters Active decisions by outcome_score >= 0."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.cognition.brief import compose_brief
from agent_memory_lite.utils.time import iso_now

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
OUTCOME_PATH = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0002_outcome_loop.sql"
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
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(OUTCOME_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


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
        "outcome_score": 0.0,
        "pinned": 0,
        "gist": "g",
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    qs = ", ".join("?" for _ in defaults)
    conn.execute(f"INSERT INTO decisions ({cols}) VALUES ({qs})", tuple(defaults.values()))
    conn.commit()


def test_negative_outcome_decision_excluded_from_active(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id="dec_good", gist="Good decision", outcome_score=0.4)
    _seed_decision(conn, id="dec_bad", gist="Failed approach", outcome_score=-0.5)
    brief = compose_brief(conn, workspace_id="ws")
    # Split body into sections so we assert exclusion within Active scope.
    active_section, _, watch_section = brief.body_md.partition("## Watch-outs")
    assert "dec_good" in active_section
    # Bad decision must NOT appear in Active decisions section.
    assert "dec_bad" not in active_section
    # But it DOES appear under Watch-outs.
    assert "dec_bad" in watch_section
    assert "Failed approach" in watch_section


def test_pinned_negative_outcome_still_appears_in_active(conn: sqlite3.Connection) -> None:
    """Operator-pinned rules survive a negative outcome; they sort beneath
    positive peers but stay visible (operator override)."""
    _seed_decision(
        conn, id="dec_pinned_bad", gist="Pinned but failing", pinned=1, outcome_score=-0.5
    )
    _seed_decision(conn, id="dec_neutral", gist="Neutral active", outcome_score=0.0)
    brief = compose_brief(conn, workspace_id="ws")
    assert "dec_pinned_bad" in brief.body_md
    assert "dec_neutral" in brief.body_md


def test_active_decisions_sorted_by_outcome(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id="dec_meh", gist="meh", outcome_score=0.1)
    _seed_decision(conn, id="dec_great", gist="great", outcome_score=0.8)
    _seed_decision(conn, id="dec_ok", gist="ok", outcome_score=0.4)
    brief = compose_brief(conn, workspace_id="ws")
    # great should appear before meh in the brief body
    idx_great = brief.body_md.find("dec_great")
    idx_meh = brief.body_md.find("dec_meh")
    assert 0 <= idx_great < idx_meh


def test_watch_outs_empty_when_all_positive(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, id="dec_g", gist="all good", outcome_score=0.6)
    brief = compose_brief(conn, workspace_id="ws")
    # Watch-outs header should not appear when there are no negatives.
    assert "## Watch-outs" not in brief.body_md
