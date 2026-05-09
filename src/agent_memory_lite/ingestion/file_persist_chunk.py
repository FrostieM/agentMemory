"""1.4.0: per-record chunk persistence helper, split from
``file_pipeline.py`` to keep the orchestrator under the SLOC ceiling.

Builds a ``ChunkIn`` from an ``IngestChunk`` record (forwarding the
new symbol metadata fields), inserts the row, and indexes both the
qualified name and any extra symbols (bare method name etc.) into
FTS so a search for ``calculate`` still hits a method whose
canonical symbol is ``paperBot.calculate``.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.fts.chunks_fts import insert_chunk_fts
from agent_memory_lite.ingestion.file_chunking import IngestChunk
from agent_memory_lite.models.chunks import Chunk, ChunkIn
from agent_memory_lite.models.enums import ChunkKind
from agent_memory_lite.repositories.chunks_repo import insert_chunk


def persist_chunk(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    file_id: str,
    episode_id: str,
    kind: ChunkKind,
    record: IngestChunk,
    path: str,
    language: str | None,
) -> tuple[Chunk, list[str]]:
    """Insert one chunk + FTS row, return (chunk, fts_symbols)."""
    chunk_metadata: dict[str, object] = {"path": path, "language": language}
    if record.extra_metadata:
        chunk_metadata.update(record.extra_metadata)
    fts_symbols = list(record.symbols)
    extras_raw = record.extra_metadata.get("extra_symbols")
    extras: list[str] = list(extras_raw) if isinstance(extras_raw, list) else []
    for extra in extras:
        if extra and extra not in fts_symbols:
            fts_symbols.append(extra)
    chunk = insert_chunk(
        conn,
        ChunkIn(
            workspace_id=workspace_id,
            file_id=file_id,
            episode_id=episode_id,
            kind=kind,
            text=record.text,
            line_start=record.line_start,
            line_end=record.line_end,
            symbols=record.symbols,
            symbol_kind=record.symbol_kind,
            qualified_name=record.qualified_name,
            parent_qualified_name=record.parent_qualified_name,
            importance=0.4,
            confidence=1.0,
            metadata=chunk_metadata,
        ),
    )
    insert_chunk_fts(
        conn,
        chunk_id=chunk.id,
        workspace_id=workspace_id,
        path=path,
        symbols=fts_symbols,
        text=chunk.text,
        summary=chunk.summary,
    )
    return chunk, fts_symbols
