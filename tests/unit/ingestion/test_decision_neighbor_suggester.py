"""Unit tests for the Move 5 decision-neighbor suggester."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.db.connection import open_connection
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.ingestion.decision_neighbor_suggester import (
    suggest_decision_neighbors,
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db_path = tmp_path / "src.db"
    c = open_connection(db_path)
    apply_migrations(c)
    yield c
    c.close()


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    decision_id: str,
    title: str,
    decision_text: str,
    rationale: str | None = None,
    status: str = "active",
) -> None:
    now = "2026-05-19T00:00:00+00:00"
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, rationale, status,
            supersedes_decision_id, source_episode_id, confidence,
            importance, valid_from, valid_to, created_at, updated_at,
            pinned)
           VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 0.9, 0.8,
                   ?, NULL, ?, ?, 0)""",
        (decision_id, workspace_id, title, decision_text, rationale, status, now, now, now),
    )
    conn.commit()


def test_returns_empty_when_workspace_has_no_decisions(conn: sqlite3.Connection) -> None:
    out = suggest_decision_neighbors(conn, workspace_id="alpha", title="T", text="body")
    assert out == []


def test_returns_empty_for_text_with_no_scoreable_tokens(conn: sqlite3.Connection) -> None:
    _seed_decision(
        conn,
        workspace_id="alpha",
        decision_id="dec_1",
        title="Kelly sizing",
        decision_text="Use quarter-Kelly for bets",
    )
    # All stopwords -> empty token set -> empty result.
    out = suggest_decision_neighbors(conn, workspace_id="alpha", title="", text="the of a")
    assert out == []


def test_ranks_overlap_decisions_higher(conn: sqlite3.Connection) -> None:
    _seed_decision(
        conn,
        workspace_id="alpha",
        decision_id="dec_overlap",
        title="Kelly sizing for prediction-market bets",
        decision_text="Use quarter-Kelly fraction; multi-leg discount handled separately.",
    )
    _seed_decision(
        conn,
        workspace_id="alpha",
        decision_id="dec_other",
        title="Database WAL mode",
        decision_text="Switch to WAL for concurrent reads.",
    )
    out = suggest_decision_neighbors(
        conn,
        workspace_id="alpha",
        title="Kelly bet sizing on prediction markets",
        text="Quarter-Kelly fraction proposed",
    )
    assert len(out) >= 1
    assert out[0].decision_id == "dec_overlap"
    assert out[0].score > 0.0


def test_exclude_id_filters_self(conn: sqlite3.Connection) -> None:
    """The just-written decision must never appear in its own neighbors."""
    _seed_decision(
        conn,
        workspace_id="alpha",
        decision_id="dec_self",
        title="Kelly sizing",
        decision_text="Use quarter-Kelly fraction",
    )
    out = suggest_decision_neighbors(
        conn,
        workspace_id="alpha",
        title="Kelly sizing",
        text="Use quarter-Kelly fraction",
        exclude_id="dec_self",
    )
    assert out == []


def test_archived_decisions_excluded(conn: sqlite3.Connection) -> None:
    """Suggester only looks at status='active' rows."""
    _seed_decision(
        conn,
        workspace_id="alpha",
        decision_id="dec_archived",
        title="Kelly sizing",
        decision_text="Quarter-Kelly proposal",
        status="archived",
    )
    out = suggest_decision_neighbors(
        conn,
        workspace_id="alpha",
        title="Kelly sizing on prediction markets",
        text="Use quarter-Kelly",
    )
    assert out == []


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    """A decision in workspace='beta' must not surface for workspace='alpha'."""
    _seed_decision(
        conn,
        workspace_id="beta",
        decision_id="dec_beta",
        title="Kelly sizing",
        decision_text="Use quarter-Kelly",
    )
    out = suggest_decision_neighbors(
        conn,
        workspace_id="alpha",
        title="Kelly sizing",
        text="Use quarter-Kelly",
    )
    assert out == []


def test_limit_caps_result_set(conn: sqlite3.Connection) -> None:
    for i in range(6):
        _seed_decision(
            conn,
            workspace_id="alpha",
            decision_id=f"dec_{i}",
            title=f"Kelly sizing variant {i}",
            decision_text="Use quarter-Kelly fraction for prediction-market bets",
        )
    out = suggest_decision_neighbors(
        conn,
        workspace_id="alpha",
        title="Kelly sizing",
        text="Use quarter-Kelly fraction",
        limit=2,
    )
    assert len(out) == 2


def test_min_score_floor(conn: sqlite3.Connection) -> None:
    """Below-threshold matches are dropped."""
    _seed_decision(
        conn,
        workspace_id="alpha",
        decision_id="dec_low",
        title="Unrelated topic",
        decision_text="Nothing about prediction markets at all.",
    )
    out = suggest_decision_neighbors(
        conn,
        workspace_id="alpha",
        title="Kelly sizing for prediction-market bets",
        text="Quarter-Kelly fraction",
        min_score=0.5,
    )
    # The unrelated decision has near-zero overlap; min_score=0.5 drops it.
    assert out == []


def test_snippet_truncated_to_120_chars(conn: sqlite3.Connection) -> None:
    long_text = "Quarter-Kelly fraction " * 30
    _seed_decision(
        conn,
        workspace_id="alpha",
        decision_id="dec_long",
        title="Kelly sizing",
        decision_text=long_text,
    )
    out = suggest_decision_neighbors(
        conn,
        workspace_id="alpha",
        title="Kelly sizing",
        text="Quarter-Kelly fraction",
    )
    assert len(out) == 1
    assert len(out[0].snippet) <= 120
