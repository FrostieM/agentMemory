"""Batch F: reliability-detector gate tests.

These prove the integrity DETECTORS actually CATCH the divergence classes they
exist to catch (a detector that never fires is worthless), and pin the durable
write -> durable_fts sync invariant (Batch B). They run in the default pytest CI
job, so a regression that lets chunk row/index FTS state silently diverge -- or a
durable writer that bypasses ``sync_durable_fts`` -- turns CI red.

The sync-bypass tests assert via ``search_kind_fts`` (the durable_fts-ONLY path),
NOT ``search`` -- ``search`` falls back to a LIKE substring scan over the source
table on zero FTS hits, which would find the row even with durable_fts empty and
make the guard tautological.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.fts.chunks_fts import insert_chunk_fts
from agent_memory_lite.ingestion.canonical_writer import write_canonical
from agent_memory_lite.maintenance.integrity_fts import fts_check
from agent_memory_lite.models.chunks import ChunkIn
from agent_memory_lite.models.enums import ChunkKind
from agent_memory_lite.repositories.chunks_repo import insert_chunk
from agent_memory_lite.storage.reader import search_kind_fts

_WS = "default"


def _seed_chunk_with_fts(conn: sqlite3.Connection, text: str) -> str:
    chunk = insert_chunk(
        conn,
        ChunkIn(workspace_id=_WS, kind=ChunkKind.EPISODE, text=text, summary=None),
    )
    insert_chunk_fts(
        conn,
        chunk_id=chunk.id,
        workspace_id=_WS,
        path=None,
        symbols=[],
        text=text,
        summary=None,
    )
    return chunk.id


# ============================================================
# row-vs-index divergence (chunks vs chunks_fts)
# ============================================================


def test_fts_check_is_ok_on_consistent_state(applied_conn: sqlite3.Connection) -> None:
    _seed_chunk_with_fts(applied_conn, "alpha beta gamma")
    check = fts_check(applied_conn, _WS)
    assert check.status == "ok"
    assert check.details["missing"] == 0
    assert check.details["extra"] == 0


def test_fts_check_detects_missing_index_row(applied_conn: sqlite3.Connection) -> None:
    """A chunk whose FTS row is gone (row present, index missing) -- the divergence
    a writer that forgot to index would create -- must be DETECTED, not silent."""
    chunk_id = _seed_chunk_with_fts(applied_conn, "alpha beta gamma")
    applied_conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
    applied_conn.commit()
    check = fts_check(applied_conn, _WS)
    assert check.status == "degraded"
    assert check.details["missing"] >= 1


def test_fts_check_detects_orphan_index_row(applied_conn: sqlite3.Connection) -> None:
    """An FTS row whose chunk is gone (index present, row missing) -- the inverse
    divergence -- must be DETECTED."""
    chunk_id = _seed_chunk_with_fts(applied_conn, "alpha beta gamma")
    applied_conn.execute("DELETE FROM chunks WHERE id = ?", (chunk_id,))
    applied_conn.commit()
    check = fts_check(applied_conn, _WS)
    assert check.status == "degraded"
    assert check.details["extra"] >= 1


# ============================================================
# durable write -> FTS sync invariant (Batch B sync-bypass guard)
# ============================================================


def test_durable_decision_is_indexed_in_durable_fts(applied_conn: sqlite3.Connection) -> None:
    """The Batch B invariant: a durable decision written via the canonical path is
    indexed in durable_fts. Asserted via search_kind_fts (the durable_fts-ONLY
    path) -- NOT search(), whose LIKE fallback would find the source row even with
    durable_fts empty. So this DOES fail if a writer bypasses sync_durable_fts."""
    out = write_canonical(
        applied_conn,
        workspace_id=_WS,
        kind="decision",
        payload={
            "title": "kelly fractional sizing rule",
            "decision_text": "Size positions at quarter kelly to bound drawdown.",
        },
    )
    assert out is not None
    hits = search_kind_fts(
        applied_conn, workspace_id=_WS, kind="decision", query="kelly fractional sizing", limit=10
    )
    assert any(str(h.projection.get("id")) == str(out["id"]) for h in hits), (
        "durable decision not in durable_fts after write -- a durable writer bypassed "
        "sync_durable_fts (Batch B regression)"
    )


def test_durable_theory_is_indexed_in_durable_fts(applied_conn: sqlite3.Connection) -> None:
    out = write_canonical(
        applied_conn,
        workspace_id=_WS,
        kind="theory",
        payload={
            "title": "half kelly volatility thesis",
            "claim": "Half kelly retains most growth while halving variance.",
        },
    )
    assert out is not None
    hits = search_kind_fts(
        applied_conn, workspace_id=_WS, kind="theory", query="half kelly volatility", limit=10
    )
    assert any(str(h.projection.get("id")) == str(out["id"]) for h in hits), (
        "durable theory not in durable_fts after write -- sync_durable_fts bypassed"
    )
