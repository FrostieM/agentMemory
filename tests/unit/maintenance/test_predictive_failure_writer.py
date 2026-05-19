"""Persist v3.1 predictive warnings as memory_candidate rows."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.maintenance.predictive_failure import PredictiveWarning
from agent_memory_lite.maintenance.predictive_failure_writer import (
    candidate_id_for,
    persist_warning,
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    c = open_connection(tmp_path / "src.db")
    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _seed_episode(conn: sqlite3.Connection, ep_id: str) -> None:
    conn.execute(
        "INSERT INTO episodes (id, workspace_id, source_type, raw_text, created_at) "
        "VALUES (?, 'ws', 'agent_action', 'fixture', '2026-05-19T00:00:00+00:00')",
        (ep_id,),
    )
    conn.commit()


def test_candidate_id_includes_both_decisions() -> None:
    cid = candidate_id_for("dec_fresh_a", "dec_failed_b")
    assert cid == "cand_pw_dec_fresh_a__dec_failed_b"


def test_persist_warning_writes_candidate_row(conn: sqlite3.Connection) -> None:
    _seed_episode(conn, "ep_persist_a")
    warning = PredictiveWarning(
        fresh_decision_id="dec_fresh",
        failed_decision_id="dec_failed",
        similarity=0.65,
        reason_text="Decision dec_fresh token-overlaps decision dec_failed",
    )
    cid = persist_warning(
        conn,
        workspace_id="ws",
        warning=warning,
        source_episode_id="ep_persist_a",
    )
    assert cid == "cand_pw_dec_fresh__dec_failed"
    row = conn.execute(
        "SELECT kind, subject, predicate, object, confidence, status "
        "FROM memory_candidates WHERE id = ?",
        (cid,),
    ).fetchone()
    assert row is not None
    assert row["kind"] == "predictive_warning"
    assert row["subject"] == "dec_fresh"
    assert row["predicate"] == "lookalike_of"
    assert row["object"] == "dec_failed"
    assert row["confidence"] == pytest.approx(0.65)
    assert row["status"] == "new"


def test_persist_warning_skips_when_no_source_episode(conn: sqlite3.Connection) -> None:
    warning = PredictiveWarning(
        fresh_decision_id="dec_fresh",
        failed_decision_id="dec_failed",
        similarity=0.5,
        reason_text="reason",
    )
    cid = persist_warning(conn, workspace_id="ws", warning=warning, source_episode_id="")
    assert cid == ""


def test_persist_warning_is_idempotent(conn: sqlite3.Connection) -> None:
    """Re-running on the same warning pair updates in place."""
    _seed_episode(conn, "ep_idem")
    w1 = PredictiveWarning(
        fresh_decision_id="dec_a",
        failed_decision_id="dec_b",
        similarity=0.5,
        reason_text="v1 reason",
    )
    w2 = PredictiveWarning(
        fresh_decision_id="dec_a",
        failed_decision_id="dec_b",
        similarity=0.7,
        reason_text="v2 reason (regenerated)",
    )
    out1 = persist_warning(conn, workspace_id="ws", warning=w1, source_episode_id="ep_idem")
    out2 = persist_warning(conn, workspace_id="ws", warning=w2, source_episode_id="ep_idem")
    assert out1 == out2 == "cand_pw_dec_a__dec_b"
    rows = conn.execute(
        "SELECT object, evidence, confidence FROM memory_candidates WHERE subject = 'dec_a'"
    ).fetchall()
    assert len(rows) == 1
    # ``object`` carries the failed_decision_id; the regenerated reason
    # lands in ``evidence`` per the writer schema.
    assert rows[0]["object"] == "dec_b"
    assert "v2 reason" in rows[0]["evidence"]
    assert rows[0]["confidence"] == pytest.approx(0.7)


def test_persist_warning_preserves_promoted_status(conn: sqlite3.Connection) -> None:
    """After operator promotion, re-running does not clobber the body."""
    _seed_episode(conn, "ep_promo")
    warning_v1 = PredictiveWarning(
        fresh_decision_id="dec_x",
        failed_decision_id="dec_y",
        similarity=0.5,
        reason_text="v1 reason",
    )
    out1 = persist_warning(
        conn, workspace_id="ws", warning=warning_v1, source_episode_id="ep_promo"
    )
    conn.execute("UPDATE memory_candidates SET status = 'accepted' WHERE id = ?", (out1,))
    conn.commit()
    warning_v2 = PredictiveWarning(
        fresh_decision_id="dec_x",
        failed_decision_id="dec_y",
        similarity=0.9,
        reason_text="v2 reason regenerated",
    )
    out2 = persist_warning(
        conn, workspace_id="ws", warning=warning_v2, source_episode_id="ep_promo"
    )
    assert out2 == ""  # rowcount=0 → skipped
    row = conn.execute(
        "SELECT object, evidence, confidence, status FROM memory_candidates WHERE id = ?",
        (out1,),
    ).fetchone()
    assert row["status"] == "accepted"
    assert "v1 reason" in row["evidence"]
    assert row["confidence"] == pytest.approx(0.5)


def test_persist_warning_returns_empty_on_fk_violation(
    conn: sqlite3.Connection,
) -> None:
    """FK violation (nonexistent episode) returns "" rather than crashing."""
    warning = PredictiveWarning(
        fresh_decision_id="dec_a",
        failed_decision_id="dec_b",
        similarity=0.5,
        reason_text="reason",
    )
    conn.execute("PRAGMA foreign_keys = ON")
    out = persist_warning(
        conn, workspace_id="ws", warning=warning, source_episode_id="ep_nonexistent"
    )
    assert out == ""
