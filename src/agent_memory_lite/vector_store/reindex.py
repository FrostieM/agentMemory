"""Rebuild the chunks vector namespace from the SQLite `chunks` table.

Used when the embedding model changes (dim drift) or when the vector store
gets out of sync. The flow:

1. Drop the chunks namespace.
2. Stream rows from `chunks`, batch-embed.
3. Upsert into the store with metadata that lets retrieval surface a hit
   without a second SQLite trip.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.batching import iter_batches
from agent_memory_lite.vector_store.base import VectorRow, VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS

DEFAULT_BATCH_SIZE = 32


def _stream_chunks(
    conn: sqlite3.Connection, workspace_id: str
) -> Iterator[tuple[str, str, str, str | None, str | None, str | None]]:
    rows = conn.execute(
        """
        SELECT c.id, c.workspace_id, c.text, c.kind, c.episode_id, f.path
        FROM chunks c
        LEFT JOIN files f ON f.id = c.file_id
        WHERE c.workspace_id = ?
        ORDER BY c.created_at
        """,
        (workspace_id,),
    )
    for row in rows:
        yield (row[0], row[1], row[2], row[3], row[4], row[5])


def reindex_chunks(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    provider: EmbeddingProvider,
    store: VectorStore,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    store.open()
    store.drop_namespace(NAMESPACE_CHUNKS)

    pending: list[tuple[str, str, str, dict[str, str | None]]] = []
    total = 0
    for chunk_id, ws, text, kind, episode_id, path in _stream_chunks(conn, workspace_id):
        meta = {
            "chunk_id": chunk_id,
            "kind": kind,
            "episode_id": episode_id,
            "path": path,
        }
        pending.append((chunk_id, ws, text, meta))

    for batch in iter_batches(pending, batch_size):
        texts = [item[2] for item in batch]
        vectors = provider.embed_batch(texts, kind="doc")
        rows = [
            VectorRow(
                id=item[0],
                workspace_id=item[1],
                vector=vectors[idx],
                metadata=item[3],
            )
            for idx, item in enumerate(batch)
        ]
        store.upsert(NAMESPACE_CHUNKS, rows)
        total += len(rows)

    return total
