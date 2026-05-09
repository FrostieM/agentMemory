"""Per-chunk persistence loop, split from ``file_post_chunk.py``.

Iterates over ``IngestChunk`` records, writes each non-empty chunk
+ FTS row via ``persist_chunk``, returns three lists aligned by
index for use by the rest of the pipeline:

* ``new_chunk_ids``      — (chunk_id, text, fts_symbols) tuples used
                            by the vector-store phase
* ``new_chunk_qnames``   — (chunk_id, qualified_name) tuples used by
                            the edge resolver
* ``new_chunks_full``    — full Chunk records used by the version
                            recorder + digest builder
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.file_chunking import IngestChunk
from agent_memory_lite.ingestion.file_persist_chunk import persist_chunk
from agent_memory_lite.models.chunks import Chunk
from agent_memory_lite.models.enums import ChunkKind


def persist_chunks_loop(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    file_id: str,
    episode_id: str,
    kind: ChunkKind,
    records: list[IngestChunk],
    path: str,
    language: str | None,
) -> tuple[
    list[tuple[str, str, list[str]]],
    list[tuple[str, str | None]],
    list[Chunk],
]:
    new_chunk_ids: list[tuple[str, str, list[str]]] = []
    new_chunk_qnames: list[tuple[str, str | None]] = []
    new_chunks_full: list[Chunk] = []
    for record in records:
        if not record.text.strip():
            continue
        chunk, fts_symbols = persist_chunk(
            conn,
            workspace_id=workspace_id,
            file_id=file_id,
            episode_id=episode_id,
            kind=kind,
            record=record,
            path=path,
            language=language,
        )
        new_chunk_ids.append((chunk.id, chunk.text, fts_symbols))
        new_chunk_qnames.append((chunk.id, chunk.qualified_name))
        new_chunks_full.append(chunk)
    return new_chunk_ids, new_chunk_qnames, new_chunks_full
