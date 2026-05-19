"""Brief surfaces v3.1 active-memory sections:

* ``## Open proposals (v3.1 Vector 1)`` — pending theory-proposal
  candidates emitted by the heuristic scanner.
* ``## Predictive warnings (v3.1 Vector 5)`` — decisions that
  token-overlap known low-outcome failures.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.cognition import brief as brief_mod
from agent_memory_lite.cognition.brief import compose_brief

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
OUTCOME_PATH = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0002_outcome_loop.sql"
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    brief_mod._BRIEF_CACHE.clear()
    brief_mod.reset_session_seen()
    monkeypatch.delenv("MEMORY_EXPERIMENT_PROPOSAL_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_PREDICTIVE_FAILURE_ENABLED", raising=False)
    yield
    brief_mod._BRIEF_CACHE.clear()
    brief_mod.reset_session_seen()


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Production-like hybrid schema: legacy migrations apply first
    (creating ``memory_candidates``, ``episodes``, ``decisions``), then
    canonical overlays add ``insights`` + ``outcome_score`` columns."""
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    c = open_connection(tmp_path / "brief.db")
    apply_migrations(c)
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(OUTCOME_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_proposal_candidate(
    conn: sqlite3.Connection, *, cand_id: str, insight_id: str, hypothesis: str
) -> None:
    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES (?, 'ws', 'agent_action', 'fixture', '2026-05-19T00:00:00+00:00')",
        (f"ep_{cand_id}",),
    )
    conn.execute(
        """INSERT INTO memory_candidates (
            id, workspace_id, kind, subject, predicate, object, evidence,
            confidence, importance, trust_level, temporal_json,
            write_targets_json, metadata_json, source_episode_id,
            status, created_at, updated_at
        ) VALUES (?, 'ws', 'theory_proposal', ?, 'proposes_theory', ?, '',
                  0.55, 0.5, 'agent_observed', '{}', '[]', '{}', ?,
                  'new', '2026-05-19T00:00:00+00:00',
                  '2026-05-19T00:00:00+00:00')""",
        (cand_id, insight_id, hypothesis, f"ep_{cand_id}"),
    )
    conn.commit()


def _seed_decision_pair(
    conn: sqlite3.Connection,
    *,
    fresh_id: str,
    failed_id: str,
    overlap_text: str,
) -> None:
    """Seed a failed + fresh decision pair so Vector 5 surfaces a warning."""
    for did, oscore, upd in (
        (failed_id, -0.7, "2026-01-01T00:00:00+00:00"),
        (fresh_id, 0.0, "2026-03-01T00:00:00+00:00"),
    ):
        conn.execute(
            """INSERT INTO decisions (
                id, workspace_id, title, decision_text, rationale,
                outcome_score, status, valid_from, created_at, updated_at
            ) VALUES (?, 'ws', ?, ?, '', ?, 'active', ?, ?, ?)""",
            (did, overlap_text, overlap_text, oscore, upd, upd, upd),
        )
    conn.commit()


def test_brief_surfaces_open_proposals(conn: sqlite3.Connection) -> None:
    """Pending theory-proposal candidates appear in the brief."""
    _seed_proposal_candidate(
        conn,
        cand_id="cand_prop_ins_kelly",
        insight_id="ins_kelly",
        hypothesis="Hypothesis: quarter-Kelly bet sizing improves drawdown",
    )
    brief = compose_brief(conn, workspace_id="ws", task=None)
    text = brief.body_md
    assert "## Open proposals (v3.1 Vector 1)" in text
    assert "cand_prop_ins_kelly" in text
    assert "ins_kelly" in text


def test_brief_omits_proposals_section_when_empty(conn: sqlite3.Connection) -> None:
    """No pending proposals → section header not in brief (empty section
    contributes zero lines after fit_to_budget)."""
    brief = compose_brief(conn, workspace_id="ws", task=None)
    text = brief.body_md
    assert "## Open proposals" not in text


def test_brief_omits_proposals_when_status_not_new(conn: sqlite3.Connection) -> None:
    """Promoted/rejected candidates do not surface — the brief only
    shows the still-pending ones."""
    _seed_proposal_candidate(
        conn,
        cand_id="cand_prop_ins_promoted",
        insight_id="ins_promoted",
        hypothesis="Hypothesis: rejected-pattern detection",
    )
    conn.execute(
        "UPDATE memory_candidates SET status = 'accepted' WHERE id = ?",
        ("cand_prop_ins_promoted",),
    )
    conn.commit()
    brief = compose_brief(conn, workspace_id="ws", task=None)
    text = brief.body_md
    assert "## Open proposals" not in text


def test_brief_surfaces_predictive_warnings(conn: sqlite3.Connection) -> None:
    """A fresh decision overlapping a low-outcome decision surfaces in brief."""
    _seed_decision_pair(
        conn,
        fresh_id="dec_fresh_kelly",
        failed_id="dec_failed_kelly",
        overlap_text="kelly trading drawdown sizing approach quarter",
    )
    brief = compose_brief(conn, workspace_id="ws", task=None)
    text = brief.body_md
    assert "## Predictive warnings (v3.1 Vector 5)" in text
    assert "dec_fresh_kelly" in text
    assert "dec_failed_kelly" in text


def test_brief_omits_predictive_when_disabled(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEMORY_PREDICTIVE_FAILURE_ENABLED", "false")
    _seed_decision_pair(
        conn,
        fresh_id="dec_x",
        failed_id="dec_y",
        overlap_text="kelly trading drawdown sizing approach quarter",
    )
    brief = compose_brief(conn, workspace_id="ws", task=None)
    text = brief.body_md
    assert "## Predictive warnings" not in text


def test_brief_token_budget_still_under_500(conn: sqlite3.Connection) -> None:
    """Brief stays under the 500-token target even with the new
    sections added. We seed both Vector 1 + Vector 5 content to
    exercise the budget redistribution path."""
    _seed_proposal_candidate(
        conn,
        cand_id="cand_prop_ins_a",
        insight_id="ins_a",
        hypothesis="Hypothesis short body",
    )
    _seed_decision_pair(
        conn,
        fresh_id="dec_fresh_z",
        failed_id="dec_failed_z",
        overlap_text="kelly trading drawdown sizing approach quarter",
    )
    brief = compose_brief(conn, workspace_id="ws", task=None)
    # Default DEFAULT_TOKEN_BUDGET=500. Brief should stay near or under.
    assert brief.token_count <= 500
    assert "## Open proposals" in brief.body_md
    assert "## Predictive warnings" in brief.body_md
