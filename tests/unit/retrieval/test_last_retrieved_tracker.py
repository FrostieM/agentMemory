"""Pin the last_retrieved_at update + audit-batching policy.

Pure-function policy is tested without a DB. The DB-backed update path is
exercised against the real migrations so column-add and update SQL are
both validated.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.canonical_writer import write_canonical
from agent_memory_lite.retrieval.last_retrieved_tracker import (
    SUPPORTED_KINDS,
    RetrievalUpdate,
    decide_audit_emission,
    mark_retrieved,
    mark_retrieved_hits,
)
from agent_memory_lite.storage.reader import search


class _Hit:
    """Duck-typed SearchHit (kind + projection['id']) for tracker unit tests."""

    def __init__(self, kind: str, row_id: str) -> None:
        self.kind = kind
        self.projection = {"id": row_id}


def _seed_chunk(conn: sqlite3.Connection, *, chunk_id: str = "ch_a") -> None:
    conn.execute(
        """
        INSERT INTO chunks
        (id, workspace_id, file_id, episode_id, kind, text, summary,
         line_start, line_end, symbols_json, embedding_id, importance,
         confidence, created_at)
        VALUES (?, 'default', NULL, NULL, 'episode', 'body', NULL,
                NULL, NULL, '[]', NULL, 0.5, 1.0,
                '2025-01-01T00:00:00Z')
        """,
        (chunk_id,),
    )


def _read_last_retrieved(conn: sqlite3.Connection, chunk_id: str) -> str | None:
    row = conn.execute("SELECT last_retrieved_at FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
    return None if row is None or row[0] is None else str(row[0])


def test_supported_kinds_constant_is_complete() -> None:
    assert set(SUPPORTED_KINDS) == {"chunk", "decision", "theory", "concept"}


def test_audit_emission_disabled_when_batch_zero() -> None:
    # batch_size=0 means "always emit"
    assert decide_audit_emission(pending_count=1, batch_size=0) is True


def test_audit_emission_below_batch_does_not_fire() -> None:
    assert decide_audit_emission(pending_count=5, batch_size=100) is False


def test_audit_emission_at_batch_fires() -> None:
    assert decide_audit_emission(pending_count=100, batch_size=100) is True


def test_mark_retrieved_writes_timestamp_to_chunk(applied_conn: sqlite3.Connection) -> None:
    _seed_chunk(applied_conn, chunk_id="ch_x")
    assert _read_last_retrieved(applied_conn, "ch_x") is None
    updated = mark_retrieved(
        applied_conn,
        workspace_id="default",
        updates=[RetrievalUpdate(kind="chunk", ids=("ch_x",))],
        audit_batch_size=1,
    )
    assert updated == 1
    assert _read_last_retrieved(applied_conn, "ch_x") is not None


def test_mark_retrieved_with_unknown_id_is_noop(applied_conn: sqlite3.Connection) -> None:
    updated = mark_retrieved(
        applied_conn,
        workspace_id="default",
        updates=[RetrievalUpdate(kind="chunk", ids=("missing",))],
    )
    assert updated == 0


def test_mark_retrieved_skips_unknown_kind(applied_conn: sqlite3.Connection) -> None:
    updated = mark_retrieved(
        applied_conn,
        workspace_id="default",
        updates=[RetrievalUpdate(kind="bogus", ids=("anything",))],
    )
    assert updated == 0


def test_audit_row_emitted_only_when_batch_threshold_reached(
    applied_conn: sqlite3.Connection,
) -> None:
    _seed_chunk(applied_conn, chunk_id="ch_a")
    # Single update with audit_batch_size=100 must NOT emit an audit row.
    mark_retrieved(
        applied_conn,
        workspace_id="default",
        updates=[RetrievalUpdate(kind="chunk", ids=("ch_a",))],
        audit_batch_size=100,
    )
    rows = applied_conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'retrieval.last_retrieved_updated'"
    ).fetchone()
    assert int(rows[0]) == 0

    # Same call with audit_batch_size=1 emits exactly one audit row.
    mark_retrieved(
        applied_conn,
        workspace_id="default",
        updates=[RetrievalUpdate(kind="chunk", ids=("ch_a",))],
        audit_batch_size=1,
    )
    rows = applied_conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'retrieval.last_retrieved_updated'"
    ).fetchone()
    assert int(rows[0]) == 1


def test_empty_updates_returns_zero(applied_conn: sqlite3.Connection) -> None:
    assert mark_retrieved(applied_conn, workspace_id="default", updates=[]) == 0


def test_mark_retrieved_hits_stamps_supported_kind(applied_conn: sqlite3.Connection) -> None:
    _seed_chunk(applied_conn, chunk_id="ch_h")
    updated = mark_retrieved_hits(
        applied_conn, workspace_id="default", hits=[_Hit("chunk", "ch_h")]
    )
    assert updated == 1
    assert _read_last_retrieved(applied_conn, "ch_h") is not None


def test_mark_retrieved_hits_ignores_unsupported_kind(applied_conn: sqlite3.Connection) -> None:
    # behaviors are not ranking-recency-tracked; a hit of an unsupported kind is skipped.
    assert (
        mark_retrieved_hits(applied_conn, workspace_id="default", hits=[_Hit("behavior", "beh_x")])
        == 0
    )


def test_search_stamps_last_retrieved_at_on_top_k(applied_conn: sqlite3.Connection) -> None:
    """H8 (global-audit 2026-06-30): the dead caller is now wired -- a real search()
    stamps last_retrieved_at on the rows it returns, so the cold-memory lifecycle is
    no longer inert. Before this fix mark_retrieved had NO caller."""
    row = write_canonical(
        applied_conn,
        kind="decision",
        payload={
            "title": "FTS5 ranking",
            "decision_text": "Use FTS5 BM25 for durable kinds lexical ranking",
            "rationale": "x",
            "workspace_id": "default",
        },
        workspace_id="default",
    )
    did = row["id"] if isinstance(row, dict) else row.get("id")
    before = applied_conn.execute(
        "SELECT last_retrieved_at FROM decisions WHERE id = ?", (did,)
    ).fetchone()[0]
    assert before is None

    hits = search(
        applied_conn,
        workspace_id="default",
        query="FTS5 BM25 durable lexical ranking",
        kinds=["decision"],
        limit=5,
    )
    assert hits  # the decision is retrievable

    after = applied_conn.execute(
        "SELECT last_retrieved_at FROM decisions WHERE id = ?", (did,)
    ).fetchone()[0]
    assert after is not None  # the read stamped recency


def test_internal_read_does_not_stamp_last_retrieved_at(applied_conn: sqlite3.Connection) -> None:
    """Internal callers (log_coactivations=False, e.g. the brief resolving associates)
    must NOT stamp recency, or the brief would keep every row perpetually warm and
    defeat cold detection."""
    row = write_canonical(
        applied_conn,
        kind="decision",
        payload={
            "title": "cold vectors",
            "decision_text": "Second cold decision about the vectors index",
            "rationale": "x",
            "workspace_id": "default",
        },
        workspace_id="default",
    )
    did = row["id"] if isinstance(row, dict) else row.get("id")
    search(
        applied_conn,
        workspace_id="default",
        query="Second cold decision vectors index",
        kinds=["decision"],
        limit=5,
        log_coactivations=False,
    )
    after = applied_conn.execute(
        "SELECT last_retrieved_at FROM decisions WHERE id = ?", (did,)
    ).fetchone()[0]
    assert after is None
