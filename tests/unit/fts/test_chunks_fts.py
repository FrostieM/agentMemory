from __future__ import annotations

import sqlite3

from agent_memory_lite.fts.chunks_fts import (
    delete_chunk_fts,
    insert_chunk_fts,
    rebuild_chunks_fts,
)
from agent_memory_lite.fts.query import search_chunks_fts
from agent_memory_lite.models.chunks import ChunkIn
from agent_memory_lite.models.enums import ChunkKind
from agent_memory_lite.repositories.chunks_repo import insert_chunk


def _seed_chunk(conn: sqlite3.Connection, text: str, *, summary: str | None = None) -> str:
    chunk = insert_chunk(
        conn,
        ChunkIn(
            workspace_id="default",
            kind=ChunkKind.EPISODE,
            text=text,
            summary=summary,
        ),
    )
    insert_chunk_fts(
        conn,
        chunk_id=chunk.id,
        workspace_id="default",
        path=None,
        symbols=[],
        text=text,
        summary=summary,
    )
    return chunk.id


def test_insert_and_search_finds_exact_token(applied_conn: sqlite3.Connection) -> None:
    chunk_id = _seed_chunk(applied_conn, "Implemented retrieval for chunks_fts")
    hits = search_chunks_fts(applied_conn, workspace_id="default", query="retrieval", limit=10)
    assert any(hit.chunk_id == chunk_id for hit in hits)


def test_search_respects_workspace(applied_conn: sqlite3.Connection) -> None:
    _seed_chunk(applied_conn, "alpha bravo charlie")
    hits_other = search_chunks_fts(applied_conn, workspace_id="other", query="alpha", limit=10)
    assert hits_other == []


def test_delete_chunk_fts_removes_row(applied_conn: sqlite3.Connection) -> None:
    chunk_id = _seed_chunk(applied_conn, "delta echo")
    removed = delete_chunk_fts(applied_conn, chunk_id)
    assert removed == 1
    hits = search_chunks_fts(applied_conn, workspace_id="default", query="delta", limit=10)
    assert hits == []


def test_search_handles_special_chars(applied_conn: sqlite3.Connection) -> None:
    _seed_chunk(applied_conn, "stack trace at retrieval.py:42 RuntimeError")
    hits = search_chunks_fts(applied_conn, workspace_id="default", query="RuntimeError", limit=10)
    assert hits


def test_rebuild_repopulates(applied_conn: sqlite3.Connection) -> None:
    chunk_id = _seed_chunk(applied_conn, "rebuild target text")
    delete_chunk_fts(applied_conn, chunk_id)
    inserted = rebuild_chunks_fts(applied_conn, workspace_id="default")
    assert inserted >= 1
    hits = search_chunks_fts(applied_conn, workspace_id="default", query="rebuild", limit=10)
    assert any(hit.chunk_id == chunk_id for hit in hits)


def test_search_returns_empty_for_blank_query(applied_conn: sqlite3.Connection) -> None:
    _seed_chunk(applied_conn, "anything")
    assert search_chunks_fts(applied_conn, workspace_id="default", query="   ", limit=5) == []
