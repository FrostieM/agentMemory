"""Cold scanner finds rows whose last_retrieved_at is past the threshold.

Covers: pinning is honoured, NULL-skip, threshold trigger, batched audit
emission via emit_cold_candidate_events.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from agent_memory_lite.maintenance.cold_scanner import (
    ColdCandidate,
    emit_cold_candidate_events,
    find_cold_candidates,
)


def _seed_chunk(conn: sqlite3.Connection, *, chunk_id: str, last_retrieved_at: str | None) -> None:
    conn.execute(
        """
        INSERT INTO chunks
        (id, workspace_id, file_id, episode_id, kind, text, summary,
         line_start, line_end, symbols_json, embedding_id, importance,
         confidence, created_at, last_retrieved_at)
        VALUES (?, 'default', NULL, NULL, 'episode', 'body', NULL,
                NULL, NULL, '[]', NULL, 0.5, 1.0,
                '2025-01-01T00:00:00Z', ?)
        """,
        (chunk_id, last_retrieved_at),
    )


def _seed_decision(
    conn: sqlite3.Connection,
    *,
    decision_id: str,
    last_retrieved_at: str | None,
    pinned: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO decisions
        (id, workspace_id, title, decision_text, rationale, status,
         confidence, source_episode_id, supersedes_decision_id, valid_from,
         valid_to, created_at, updated_at, last_retrieved_at, pinned)
        VALUES (?, 'default', ?, 'body', '', 'active', 0.7, NULL, NULL,
                '2025-01-01T00:00:00Z', NULL, '2025-01-01T00:00:00Z',
                '2025-01-01T00:00:00Z', ?, ?)
        """,
        (decision_id, f"title-{decision_id}", last_retrieved_at, pinned),
    )


def test_no_rows_yields_no_candidates(applied_conn: sqlite3.Connection) -> None:
    candidates = find_cold_candidates(applied_conn, workspace_id="default", older_than_days=60)
    assert candidates == []


def test_null_last_retrieved_at_skipped(applied_conn: sqlite3.Connection) -> None:
    _seed_chunk(applied_conn, chunk_id="ch_a", last_retrieved_at=None)
    candidates = find_cold_candidates(applied_conn, workspace_id="default", older_than_days=60)
    assert candidates == []


def test_freshly_retrieved_chunk_not_cold(applied_conn: sqlite3.Connection) -> None:
    _seed_chunk(
        applied_conn,
        chunk_id="ch_a",
        last_retrieved_at=datetime.now(UTC).isoformat(),
    )
    candidates = find_cold_candidates(applied_conn, workspace_id="default", older_than_days=60)
    assert candidates == []


def test_old_chunk_is_flagged(applied_conn: sqlite3.Connection) -> None:
    long_ago = (datetime.now(UTC) - timedelta(days=120)).isoformat()
    _seed_chunk(applied_conn, chunk_id="ch_old", last_retrieved_at=long_ago)
    candidates = find_cold_candidates(applied_conn, workspace_id="default", older_than_days=60)
    assert len(candidates) == 1
    assert candidates[0].kind == "chunk"
    assert candidates[0].id == "ch_old"


def test_pinned_decision_excluded(applied_conn: sqlite3.Connection) -> None:
    long_ago = (datetime.now(UTC) - timedelta(days=120)).isoformat()
    _seed_decision(
        applied_conn,
        decision_id="dec_pinned",
        last_retrieved_at=long_ago,
        pinned=1,
    )
    candidates = find_cold_candidates(applied_conn, workspace_id="default", older_than_days=60)
    assert candidates == []


def test_unpinned_old_decision_is_flagged(applied_conn: sqlite3.Connection) -> None:
    long_ago = (datetime.now(UTC) - timedelta(days=120)).isoformat()
    _seed_decision(applied_conn, decision_id="dec_old", last_retrieved_at=long_ago)
    candidates = find_cold_candidates(applied_conn, workspace_id="default", older_than_days=60)
    assert len(candidates) == 1
    assert candidates[0].kind == "decision"
    assert candidates[0].id == "dec_old"


def test_emit_writes_one_audit_row_per_batch(applied_conn: sqlite3.Connection) -> None:
    candidates = [
        ColdCandidate(kind="chunk", id="ch_x", last_retrieved_at="2024-01-01T00:00:00Z"),
        ColdCandidate(kind="decision", id="dec_x", last_retrieved_at="2024-01-01T00:00:00Z"),
    ]
    written = emit_cold_candidate_events(
        applied_conn, workspace_id="default", candidates=candidates
    )
    assert written == 2
    audit_rows = applied_conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'maintenance.cold_candidate_emitted'"
    ).fetchone()
    assert int(audit_rows[0]) == 1


def test_empty_emit_writes_no_audit(applied_conn: sqlite3.Connection) -> None:
    written = emit_cold_candidate_events(applied_conn, workspace_id="default", candidates=[])
    assert written == 0
    audit_rows = applied_conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'maintenance.cold_candidate_emitted'"
    ).fetchone()
    assert int(audit_rows[0]) == 0
