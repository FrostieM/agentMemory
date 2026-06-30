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
from agent_memory_lite.repositories.chunks_repo import (
    EMBEDDING_STALE_REASON_KEY,
    EMBEDDING_TEXT_SHA256_KEY,
    chunk_text_sha256,
    set_many_chunk_embedding_ids,
)
from agent_memory_lite.repositories.vector_metadata_repo import upsert_vector_index_metadata
from agent_memory_lite.utils.time import iso_now
from agent_memory_lite.vector_store.base import VectorRow, VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS

DEFAULT_BATCH_SIZE = 32
_CHECKPOINT_META_KEY = "chunk_rebuild_progress"

# Type alias for the progress callback: (done, total) -> None
ProgressCb = Callable[[int, int], None]


def _metadata_dict(raw: object) -> dict[str, object]:
    if not isinstance(raw, str | bytes | bytearray):
        return {}
    try:
        metadata = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _chunk_needs_reembedding(
    *,
    chunk_id: str,
    text: str,
    metadata_json: object,
    existing_ids: set[str],
) -> bool:
    if chunk_id not in existing_ids:
        return True
    metadata = _metadata_dict(metadata_json)
    stored_hash = metadata.get(EMBEDDING_TEXT_SHA256_KEY)
    return (
        bool(metadata.get(EMBEDDING_STALE_REASON_KEY))
        or stored_hash is None
        or stored_hash != chunk_text_sha256(text)
    )


def _stream_chunks(
    conn: sqlite3.Connection, workspace_id: str
) -> Iterator[tuple[str, str, str, str | None, str | None, str | None, object]]:
    rows = conn.execute(
        """
        SELECT c.id, c.workspace_id, c.text, c.kind, c.episode_id, f.path, c.metadata_json
        FROM chunks c
        LEFT JOIN files f ON f.id = c.file_id
        WHERE c.workspace_id = ?
        ORDER BY c.created_at
        """,
        (workspace_id,),
    )
    for row in rows:
        yield (row[0], row[1], row[2], row[3], row[4], row[5], row[6])


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
    for chunk_id, ws, text, kind, episode_id, path, metadata_json in _stream_chunks(
        conn, workspace_id
    ):
        if not _chunk_needs_reembedding(
            chunk_id=chunk_id,
            text=text,
            metadata_json=metadata_json,
            existing_ids=existing_ids,
        ):
            continue
        meta = {
            "chunk_id": chunk_id,
            "kind": kind,
            "episode_id": episode_id,
            "path": path,
            EMBEDDING_TEXT_SHA256_KEY: chunk_text_sha256(text),
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
        set_many_chunk_embedding_ids(
            conn,
            chunk_ids=[row.id for row in rows],
            embedding_text_hashes={item[0]: chunk_text_sha256(item[2]) for item in batch},
        )
        done += len(rows)
        _write_checkpoint(conn, workspace_id=workspace_id, done=done, total=total)
        if progress_callback is not None:
            progress_callback(done, total)

    # Re-embedded stale rows are already present in existing_ids. Count the
    # store after upserts instead of estimating from "pre-existing + done".
    row_count = store.count(NAMESPACE_CHUNKS, workspace_id=workspace_id)
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


def repair_missing_vectors(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    provider: EmbeddingProvider,
    store: VectorStore,
    limit: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Embed a bounded batch of chunks that have no vector yet and upsert them.

    The incremental, self-healing complement to a full ``reindex_chunks``: a
    chunk whose embedding never landed (an interrupted write, or — once the
    write path goes async — a not-yet-embedded chunk) is detected by
    ``embedding_id IS NULL`` and repaired a batch at a time by the brain pass,
    instead of needing an operator-run rebuild. Bounded (``limit`` embeds per
    call), order-stable (oldest-missing first), and idempotent: a chunk that
    already has a vector is filtered out, and re-running upserts the same
    deterministic ``chunk_id`` row. Archived (soft-deleted) chunks are skipped
    so repair never re-introduces a removed chunk into the search namespace.
    Backed by the ``idx_chunks_missing_embedding`` partial index so the scan is
    O(missing), not O(workspace). Returns the number of vectors written.
    """
    rows = conn.execute(
        """
        SELECT c.id, c.workspace_id, c.text, c.kind, c.episode_id, f.path
        FROM chunks c
        LEFT JOIN files f ON f.id = c.file_id
        WHERE c.workspace_id = ?
          AND c.embedding_id IS NULL
          AND c.is_archived = 0
        ORDER BY c.created_at
        LIMIT ?
        """,
        (workspace_id, int(limit)),
    ).fetchall()
    if not rows:
        return 0
    store.open()
    pending: list[tuple[str, str, str, dict[str, str | None]]] = [
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            {
                "chunk_id": str(row[0]),
                "kind": row[3],
                "episode_id": row[4],
                "path": row[5],
                EMBEDDING_TEXT_SHA256_KEY: chunk_text_sha256(str(row[2])),
            },
        )
        for row in rows
    ]
    written = 0
    for batch in iter_batches(pending, DEFAULT_BATCH_SIZE):
        vectors = provider.embed_batch([item[2] for item in batch], kind="doc")
        vector_rows = [
            VectorRow(
                id=item[0],
                workspace_id=item[1],
                vector=vectors[idx],
                metadata=item[3],
            )
            for idx, item in enumerate(batch)
        ]
        # SELF-HEALING by design (Batch-E NULL-marker), NOT a missing transaction:
        # the LanceDB upsert is keyed by the deterministic chunk id, and the SQLite
        # embedding_id is gated by embedding_text_hashes. If the outer brain-pass commit
        # fails after this, embedding_id stays NULL -> the NEXT repair re-embeds the
        # same chunk and the id-keyed upsert OVERWRITES the orphan (no duplicate, no
        # loss; only redundant work). An intermediate commit here would instead break
        # the brain pass's all-or-nothing transaction, so it is deliberately omitted.
        store.upsert(NAMESPACE_CHUNKS, vector_rows)
        set_many_chunk_embedding_ids(
            conn,
            chunk_ids=[vector_row.id for vector_row in vector_rows],
            embedding_text_hashes={item[0]: chunk_text_sha256(item[2]) for item in batch},
        )
        written += len(vector_rows)
    return written


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
        SELECT id, text, metadata_json, embedding_id
        FROM chunks
        WHERE workspace_id = ?
          AND id IN ({placeholders})
        ORDER BY id
        """,
        (workspace_id, *vector_ids),
    ).fetchall()
    chunk_ids: list[str] = []
    embedding_text_hashes: dict[str, str] = {}
    for row in rows:
        chunk_id = str(row["id"])
        text_hash = chunk_text_sha256(str(row["text"]))
        metadata = _metadata_dict(row["metadata_json"])
        stored_hash = metadata.get(EMBEDDING_TEXT_SHA256_KEY)
        if metadata.get(EMBEDDING_STALE_REASON_KEY) or (
            stored_hash is not None and stored_hash != text_hash
        ):
            continue
        if stored_hash is None:
            continue
        if row["embedding_id"] is None or row["embedding_id"] != chunk_id:
            chunk_ids.append(chunk_id)
            embedding_text_hashes[chunk_id] = text_hash
    if not chunk_ids:
        return 0
    set_many_chunk_embedding_ids(
        conn,
        chunk_ids=chunk_ids,
        embedding_text_hashes=embedding_text_hashes,
    )
    return len(chunk_ids)
