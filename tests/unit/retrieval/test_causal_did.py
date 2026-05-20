"""v3.3 Vector 4 method (a) — DiD causal link extractor tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.retrieval.causal_did import (
    DidReport,
    extract_did_links,
    is_enabled,
    threshold,
)

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
OUTCOME_PATH = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0002_outcome_loop.sql"
)
CAUSAL_PATH = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0008_causal_links.sql"
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_CAUSAL_DID_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_CAUSAL_DID_THRESHOLD", raising=False)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(OUTCOME_PATH.read_text(encoding="utf-8"))
    c.executescript(CAUSAL_PATH.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    dec_id: str,
    title: str,
    outcome_score: float = 0.0,
    supersedes: str | None = None,
    workspace_id: str = "ws",
) -> None:
    conn.execute(
        """INSERT INTO decisions
           (id, workspace_id, title, decision_text, rationale,
            outcome_score, status, supersedes_decision_id,
            valid_from, created_at, updated_at)
           VALUES (?, ?, ?, ?, '', ?, 'active', ?,
                   '2026-05-19T00:00:00+00:00',
                   '2026-05-19T00:00:00+00:00',
                   '2026-05-19T00:00:00+00:00')""",
        (dec_id, workspace_id, title, title, outcome_score, supersedes),
    )
    conn.commit()


def _count_caused(conn: sqlite3.Connection, workspace_id: str = "ws") -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM causal_links WHERE workspace_id = ? AND relation = 'caused'",
        (workspace_id,),
    ).fetchone()[0]


def test_defaults() -> None:
    assert is_enabled() is True
    assert threshold() == pytest.approx(0.3)


def test_no_supersedes_no_links(conn: sqlite3.Connection) -> None:
    _seed_decision(conn, dec_id="dec_a", title="alpha", outcome_score=0.5)
    result = extract_did_links(conn, workspace_id="ws")
    assert result == DidReport(pairs_scanned=0, links_emitted=0)


def test_large_delta_emits_link(conn: sqlite3.Connection) -> None:
    """dec_new (outcome 0.8) supersedes dec_old (outcome -0.5) → big
    delta → emit causal_link(caused, weight=1.3)."""
    _seed_decision(conn, dec_id="dec_old", title="failed", outcome_score=-0.5)
    _seed_decision(conn, dec_id="dec_new", title="rescue", outcome_score=0.8, supersedes="dec_old")
    result = extract_did_links(conn, workspace_id="ws")
    assert result.pairs_scanned == 1
    assert result.links_emitted == 1
    # Verify the row exists with the right shape.
    row = conn.execute(
        "SELECT src_id, dst_id, weight FROM causal_links "
        "WHERE workspace_id = 'ws' AND relation = 'caused'"
    ).fetchone()
    assert row["src_id"] == "dec_new"
    assert row["dst_id"] == "dec_old"
    assert row["weight"] == pytest.approx(1.3)


def test_small_delta_no_link(conn: sqlite3.Connection) -> None:
    """Metadata-refresh supersede (both outcomes equal) → delta=0 →
    nothing emitted."""
    _seed_decision(conn, dec_id="dec_old", title="initial", outcome_score=0.2)
    _seed_decision(
        conn, dec_id="dec_new", title="refresh", outcome_score=0.25, supersedes="dec_old"
    )
    result = extract_did_links(conn, workspace_id="ws")
    assert result.pairs_scanned == 1
    assert result.links_emitted == 0
    assert _count_caused(conn) == 0


def test_threshold_boundary(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Delta below custom threshold is suppressed; at-or-above passes."""
    monkeypatch.setenv("MEMORY_CAUSAL_DID_THRESHOLD", "0.5")
    _seed_decision(conn, dec_id="dec_old", title="o", outcome_score=0.0)
    _seed_decision(conn, dec_id="dec_new", title="n", outcome_score=0.4, supersedes="dec_old")
    result = extract_did_links(conn, workspace_id="ws")
    assert result.links_emitted == 0
    # Make delta exactly meet the threshold.
    conn.execute("UPDATE decisions SET outcome_score = 0.5 WHERE id = 'dec_new'")
    conn.commit()
    result = extract_did_links(conn, workspace_id="ws")
    assert result.links_emitted == 1


def test_idempotent_on_rerun(conn: sqlite3.Connection) -> None:
    """Re-running the extractor on the same data emits zero new links
    (UNIQUE constraint on causal_links keeps it idempotent)."""
    _seed_decision(conn, dec_id="dec_old", title="o", outcome_score=-0.5)
    _seed_decision(conn, dec_id="dec_new", title="n", outcome_score=0.5, supersedes="dec_old")
    extract_did_links(conn, workspace_id="ws")
    second = extract_did_links(conn, workspace_id="ws")
    assert second.pairs_scanned == 1
    assert second.links_emitted == 0
    assert _count_caused(conn) == 1


def test_disabled_returns_zero(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_CAUSAL_DID_ENABLED", "false")
    _seed_decision(conn, dec_id="dec_old", title="o", outcome_score=-0.5)
    _seed_decision(conn, dec_id="dec_new", title="n", outcome_score=0.5, supersedes="dec_old")
    result = extract_did_links(conn, workspace_id="ws")
    assert result == DidReport(pairs_scanned=0, links_emitted=0)


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    """Supersede pair in workspace A must not produce links in B."""
    _seed_decision(conn, dec_id="a_old", title="o", outcome_score=-0.5, workspace_id="ws_a")
    _seed_decision(
        conn,
        dec_id="a_new",
        title="n",
        outcome_score=0.5,
        supersedes="a_old",
        workspace_id="ws_a",
    )
    extract_did_links(conn, workspace_id="ws_b")
    assert _count_caused(conn, "ws_b") == 0
    extract_did_links(conn, workspace_id="ws_a")
    assert _count_caused(conn, "ws_a") == 1


def test_failure_soft_when_table_missing() -> None:
    """Pre-migration DB without causal_links → DidReport(0,0)
    instead of raising."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.executescript(OUTCOME_PATH.read_text(encoding="utf-8"))
    # Skip causal_links migration intentionally.
    try:
        _seed_decision(c, dec_id="dec_old", title="o", outcome_score=-0.5)
        _seed_decision(c, dec_id="dec_new", title="n", outcome_score=0.5, supersedes="dec_old")
        result = extract_did_links(c, workspace_id="ws")
        assert result.pairs_scanned == 1  # we still scanned the pair
        assert result.links_emitted == 0  # but the INSERT no-op'd
    finally:
        c.close()
