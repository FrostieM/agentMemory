"""Brain-pass step: prune orphan chunk-vectors.

SQLite (the ``chunks`` table) and LanceDB (the vectors) are separate
stores with no cross-store transaction. A chunk delete (episode dedup,
archive, compaction) or an interrupted compound write commits the
SQLite side but can leave the vector behind -- an ORPHAN vector with no
backing chunk row. An orphan wastes a top-K search slot on a "dead hit":
the retrieval pipeline drops a hit whose chunk fetch returns nothing.

Nothing automatic pruned these before. The drift sentinel's vector
check measures only the MISSING direction (chunks without a vector, via
a SQLite ``embedding_id`` proxy) and never sees surplus LanceDB rows --
so orphans accumulate silently (a copyBot probe found 1024).

This module is the "sleep cleaning" step run from ``brain_pass``: every
pass it diffs the vector ids against the chunk ids (both workspace-
scoped) and deletes the surplus, capped per pass so a pathological
backlog cannot turn one tick into a giant delete. Failure-soft -- a
missing ``chunks`` table returns 0 rather than raising.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.repositories.vector_metadata_repo import set_vector_index_row_count
from agent_memory_lite.vector_store.base import VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS


def _chunk_ids(conn: sqlite3.Connection, workspace_id: str) -> set[str] | None:
    """Return the workspace's chunk ids, or ``None`` if there is no chunks table."""
    try:
        cursor = conn.execute("SELECT id FROM chunks WHERE workspace_id = ?", (workspace_id,))
    except sqlite3.OperationalError:
        return None
    return {str(row[0]) for row in cursor.fetchall()}


def prune_orphan_vectors(
    conn: sqlite3.Connection,
    store: VectorStore,
    *,
    workspace_id: str,
    max_delete: int,
    namespace: str = NAMESPACE_CHUNKS,
) -> int:
    """Delete vectors whose id has no backing chunk row in this workspace.

    Both sides are workspace-scoped, so the call is a safe no-op for any
    workspace whose vectors do not live in ``store``. Returns the number
    of orphan vectors deleted (0 when there is nothing to prune, or when
    the ``chunks`` table is absent).
    """
    chunk_ids = _chunk_ids(conn, workspace_id)
    if chunk_ids is None:
        return 0
    vector_ids = store.list_ids(namespace, workspace_id=workspace_id)
    orphans = [vid for vid in vector_ids if vid not in chunk_ids]
    if not orphans:
        return 0
    # Cap per pass: a pathological backlog must not turn one brain tick
    # into a multi-thousand-row delete; the remainder clears next pass.
    store.delete(namespace, orphans[:max_delete])
    # Recompute everything from a fresh list_ids -- never trust
    # VectorStore.delete()'s return value. It is backend-dependent
    # (LanceDBStore returns the requested id count, SqliteVecStore the
    # real rowcount), so neither the row_count cache nor the pruned-count
    # return may derive from it. SQLite's vector_index_metadata.row_count
    # and LanceDB are separate stores; without this sync an audit reports
    # metadata_status=degraded until the next full reindex. Commit so the
    # sync holds for every caller, not only the brain pass with its
    # end-of-pass commit.
    live_count = len(store.list_ids(namespace, workspace_id=workspace_id))
    set_vector_index_row_count(
        conn,
        workspace_id=workspace_id,
        namespace=namespace,
        row_count=live_count,
    )
    conn.commit()
    return len(vector_ids) - live_count
