"""Rebuild the chunks vector namespace from the SQLite `chunks` table.

Used when the embedding model changes (dim drift) or when the vector store
gets out of sync.

v3.4 streaming/checkpointed rebuild
-----------------------------------
The original implementation dropped the namespace up-front and then
embedded everything. If the process died mid-way (timeout, OOM,
hang) the operator was left with an EMPTY vector store and a
half-finished rebuild — re-running started from scratch. Today's
audit on copyBot took THREE manual reruns to converge.

The new flow is resume-safe:

* When ``resume=True`` (default), existing vectors stay in place.
  We list the IDs already in the store, then embed only the chunks
  that are NOT yet covered. Re-running picks up where the last run
  stopped.
* After every batch we (a) upsert the vectors, (b) backfill
  ``chunks.embedding_id``, (c) call ``progress_callback`` so the
  operator sees movement, and (d) write a checkpoint row to
  ``workspace_meta`` so external tooling (sentinel, UI) can poll
  progress.
* When ``resume=False`` we drop the namespace first and start clean.
  Use this only when the embedding model itself changed.

Backwards-compat: callers that pass nothing get resume-safe behaviour
which is strictly safer than the legacy drop-and-rebuild.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.embeddings.batching import iter_batches
from agent_memory_lite.repositories.chunks_repo import set_many_chunk_embedding_ids
from agent_memory_lite.repositories.vector_metadata_repo import upsert_vector_index_metadata
from agent_memory_lite.utils.time import iso_now
from agent_memory_lite.vector_store.base import VectorRow, VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS

DEFAULT_BATCH_SIZE = 32
_CHECKPOINT_META_KEY = "chunk_rebuild_progress"

# Type alias for the progress callback: (done, total) -> None
ProgressCb = Callable[[int, int], None]


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


def _write_checkpoint(
    conn: sqlite3.Connection, *, workspace_id: str, done: int, total: int
) -> None:
    """Best-effort checkpoint write. Failures here must not abort rebuild."""
    try:
        conn.execute(
            "INSERT OR REPLACE INTO workspace_meta (workspace_id, key, value, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (
                workspace_id,
                _CHECKPOINT_META_KEY,
                json.dumps({"done": done, "total": total, "at": iso_now()}),
                iso_now(),
            ),
        )
        conn.commit()
    except sqlite3.OperationalError:
        return


def _clear_checkpoint(conn: sqlite3.Connection, *, workspace_id: str) -> None:
    try:
        conn.execute(
            "DELETE FROM workspace_meta WHERE workspace_id = ? AND key = ?",
            (workspace_id, _CHECKPOINT_META_KEY),
        )
        conn.commit()
    except sqlite3.OperationalError:
        return


def reindex_chunks(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    provider: EmbeddingProvider,
    store: VectorStore,
    batch_size: int = DEFAULT_BATCH_SIZE,
    resume: bool = True,
    progress_callback: ProgressCb | None = None,
) -> int:
    """Re-embed chunks and write into the vector namespace.

    Resume-safe by default — skips chunks that already have a vector
    in the store. Pass ``resume=False`` to force a full drop+rebuild
    (only needed when the embedding model itself changed).

    ``progress_callback`` receives ``(done, total)`` after every batch
    so the caller can render a progress bar / log line. Independent
    of the checkpoint written to ``workspace_meta``.
    """
    store.open()
    if not resume:
        store.drop_namespace(NAMESPACE_CHUNKS)
        existing_ids: set[str] = set()
    else:
        existing_ids = set(store.list_ids(NAMESPACE_CHUNKS, workspace_id=workspace_id))

    pending: list[tuple[str, str, str, dict[str, str | None]]] = []
    for chunk_id, ws, text, kind, episode_id, path in _stream_chunks(conn, workspace_id):
        if chunk_id in existing_ids:
            continue
        meta = {
            "chunk_id": chunk_id,
            "kind": kind,
            "episode_id": episode_id,
            "path": path,
        }
        pending.append((chunk_id, ws, text, meta))

    # ``total`` is the count of NEW work in this call. ``done`` resets
    # every call because the checkpoint represents progress within a
    # single resume cycle, not lifetime.
    total = len(pending)
    done = 0
    if progress_callback is not None:
        progress_callback(0, total)

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
        set_many_chunk_embedding_ids(conn, chunk_ids=[row.id for row in rows])
        done += len(rows)
        _write_checkpoint(conn, workspace_id=workspace_id, done=done, total=total)
        if progress_callback is not None:
            progress_callback(done, total)

    # Final row_count = pre-existing + newly added.
    row_count = len(existing_ids) + done
    upsert_vector_index_metadata(
        conn,
        workspace_id=workspace_id,
        namespace=NAMESPACE_CHUNKS,
        provider=provider,
        store=store,
        row_count=row_count,
    )
    _clear_checkpoint(conn, workspace_id=workspace_id)
    return done


def repair_chunk_embedding_refs(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    store: VectorStore,
) -> int:
    """Backfill `chunks.embedding_id` from existing vector row ids.

    This does not embed or upsert vectors. It is safe when the vector namespace
    already has parity with SQLite chunks but older rows have NULL
    `embedding_id` values.
    """
    store.open()
    vector_ids = sorted(store.list_ids(NAMESPACE_CHUNKS, workspace_id=workspace_id))
    if not vector_ids:
        return 0
    placeholders = ",".join("?" for _ in vector_ids)
    rows = conn.execute(
        f"""
        SELECT id
        FROM chunks
        WHERE workspace_id = ?
          AND id IN ({placeholders})
          AND (embedding_id IS NULL OR embedding_id != id)
        ORDER BY id
        """,
        (workspace_id, *vector_ids),
    ).fetchall()
    chunk_ids = [str(row["id"]) for row in rows]
    if not chunk_ids:
        return 0
    set_many_chunk_embedding_ids(conn, chunk_ids=chunk_ids)
    return len(chunk_ids)
