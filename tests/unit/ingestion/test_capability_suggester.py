"""Unit tests for the Move 3 capability suggester."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_memory_lite.db.connection import open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.ingestion.capability_suggester import (
    suggest_capabilities,
    suggest_capabilities_for_decision,
)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "src.db"
    c = open_connection(db_path)
    apply_migrations(c)
    yield c
    c.close()


def _seed_skill(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    skill_id: str,
    name: str,
    summary: str,
) -> None:
    now = "2026-05-10T00:00:00+00:00"
    conn.execute(
        """INSERT INTO skills
           (id, workspace_id, name, subtype, summary, when_to_use_json, inputs_json,
            outputs_json, tools_json, related_roles_json, source_episode_id,
            confidence, active, created_at, updated_at,
            usage_count, success_count, failure_count, last_invoked_at)
           VALUES (?, ?, ?, 'skill', ?, '[]', '[]', '[]', '[]', '[]', NULL, 0.9, 1,
                   ?, ?, 0, 0, 0, NULL)""",
        (skill_id, workspace_id, name, summary, now, now),
    )
    conn.commit()


def test_returns_empty_when_workspace_has_no_capabilities(conn: sqlite3.Connection) -> None:
    out = suggest_capabilities(conn, workspace_id="alpha", text="some decision text")
    assert out == []


def test_ranks_skills_by_overlap_coefficient(conn: sqlite3.Connection) -> None:
    """A skill whose name overlaps the decision text outranks an
    unrelated skill — overlap-coefficient handles asymmetric set sizes."""
    _seed_skill(
        conn,
        workspace_id="alpha",
        skill_id="sk_match",
        name="Auth refactor",
        summary="JWT migration know-how, session cookie deprecation",
    )
    _seed_skill(
        conn,
        workspace_id="alpha",
        skill_id="sk_other",
        name="Database tuning",
        summary="WAL mode, page-size pinning, vacuum cadence",
    )
    out = suggest_capabilities(
        conn,
        workspace_id="alpha",
        text="Move auth to JWT — replace session cookies",
    )
    assert len(out) >= 1
    assert out[0].capability_name == "Auth refactor"
    assert out[0].score > 0.0


def test_ignores_other_workspace(conn: sqlite3.Connection) -> None:
    _seed_skill(
        conn,
        workspace_id="alpha",
        skill_id="sk_a",
        name="Alpha thing",
        summary="alpha-only",
    )
    out = suggest_capabilities(conn, workspace_id="beta", text="alpha thing")
    assert out == []


def test_min_score_filters_weak_matches(conn: sqlite3.Connection) -> None:
    """A skill with no token overlap scores 0 and is filtered."""
    _seed_skill(
        conn,
        workspace_id="alpha",
        skill_id="sk_off_topic",
        name="Database tuning",
        summary="WAL mode, page-size pinning",
    )
    out = suggest_capabilities(
        conn,
        workspace_id="alpha",
        text="UX redesign",  # zero overlap
    )
    assert out == []


def test_limit_caps_results(conn: sqlite3.Connection) -> None:
    """With multiple matching skills, ``limit`` caps the returned list."""
    for i in range(5):
        _seed_skill(
            conn,
            workspace_id="alpha",
            skill_id=f"sk_{i}",
            name=f"Skill {i} migration",
            summary="migration plan migration steps migration",
        )
    out = suggest_capabilities(
        conn, workspace_id="alpha", text="migration migration migration", limit=2
    )
    assert len(out) == 2


def test_for_decision_wrapper_concatenates_fields(conn: sqlite3.Connection) -> None:
    """The wrapper joins title + text + rationale before tokenizing."""
    _seed_skill(
        conn,
        workspace_id="alpha",
        skill_id="sk_match",
        name="Memory architecture",
        summary="picking storage backends",
    )
    # The match keyword "memory" is in the title only — the wrapper still
    # picks it up because it joins all three fields before scoring.
    out = suggest_capabilities_for_decision(
        conn,
        workspace_id="alpha",
        title="Use SQLite for memory",
        text="WAL is enough for our throughput.",
        rationale="no Docker, footprint matters.",
    )
    assert len(out) == 1
    assert out[0].capability_name == "Memory architecture"


def test_returns_snippet_clipped(conn: sqlite3.Connection) -> None:
    long_summary = "x " * 200  # 400 chars
    _seed_skill(
        conn,
        workspace_id="alpha",
        skill_id="sk_long",
        name="long migration overlap",
        summary=long_summary + " migration",
    )
    out = suggest_capabilities(conn, workspace_id="alpha", text="migration migration migration")
    assert len(out) == 1
    assert len(out[0].snippet) <= 80
