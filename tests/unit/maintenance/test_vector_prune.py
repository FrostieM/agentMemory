"""Paired unit test for the orphan-vector prune brain step."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import numpy as np
import pytest

from agent_memory_lite.maintenance.vector_prune import prune_orphan_vectors
from agent_memory_lite.vector_store.base import VectorRow, VectorStore
from agent_memory_lite.vector_store.namespaces import NAMESPACE_CHUNKS


@pytest.fixture
def chunks_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE chunks (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL)")
    try:
        yield conn
    finally:
        conn.close()


def _add_chunk(conn: sqlite3.Connection, chunk_id: str, workspace_id: str) -> None:
    conn.execute("INSERT INTO chunks (id, workspace_id) VALUES (?, ?)", (chunk_id, workspace_id))
    conn.commit()


def _vrow(row_id: str, workspace_id: str) -> VectorRow:
    return VectorRow(id=row_id, workspace_id=workspace_id, vector=np.zeros(4, dtype=np.float32))


def _create_metadata_table(conn: sqlite3.Connection) -> None:
    """Mirror the vector_index_metadata schema for unit tests."""
    conn.execute(
        """
        CREATE TABLE vector_index_metadata (
            workspace_id TEXT NOT NULL,
            namespace TEXT NOT NULL,
            provider_name TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL,
            vector_backend TEXT NOT NULL,
            chunking_strategy TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (workspace_id, namespace)
        )
        """
    )
    conn.commit()


def _seed_metadata_row(
    conn: sqlite3.Connection, workspace_id: str, namespace: str, row_count: int
) -> None:
    conn.execute(
        "INSERT INTO vector_index_metadata (workspace_id, namespace, provider_name, "
        "embedding_dim, vector_backend, chunking_strategy, schema_version, row_count, "
        "updated_at) VALUES (?, ?, 'st:test', 4, 'fake', 'chunking-v1', 'chunks-v1', ?, ?)",
        (workspace_id, namespace, row_count, "2020-01-01T00:00:00Z"),
    )
    conn.commit()


def test_prune_removes_orphan_vectors(chunks_conn, fake_vector_store) -> None:
    """A vector whose chunk row is gone is deleted; live ones stay."""
    for cid in ("c1", "c2", "c3"):
        _add_chunk(chunks_conn, cid, "ws")
    fake_vector_store.upsert(
        NAMESPACE_CHUNKS,
        [_vrow(rid, "ws") for rid in ("c1", "c2", "c3", "orphan1", "orphan2")],
    )

    deleted = prune_orphan_vectors(
        chunks_conn, fake_vector_store, workspace_id="ws", max_delete=100
    )

    assert deleted == 2
    assert set(fake_vector_store.list_ids(NAMESPACE_CHUNKS, workspace_id="ws")) == {
        "c1",
        "c2",
        "c3",
    }


def test_prune_no_orphans_returns_zero(chunks_conn, fake_vector_store) -> None:
    """Vectors that all have a backing chunk are left untouched."""
    for cid in ("c1", "c2"):
        _add_chunk(chunks_conn, cid, "ws")
    fake_vector_store.upsert(NAMESPACE_CHUNKS, [_vrow("c1", "ws"), _vrow("c2", "ws")])

    assert (
        prune_orphan_vectors(chunks_conn, fake_vector_store, workspace_id="ws", max_delete=100) == 0
    )
    assert set(fake_vector_store.list_ids(NAMESPACE_CHUNKS, workspace_id="ws")) == {"c1", "c2"}


def test_prune_respects_max_delete_cap(chunks_conn, fake_vector_store) -> None:
    """A backlog larger than the cap clears across passes, not in one delete."""
    _add_chunk(chunks_conn, "c1", "ws")
    orphans = [f"orphan{i}" for i in range(5)]
    fake_vector_store.upsert(NAMESPACE_CHUNKS, [_vrow(r, "ws") for r in ["c1", *orphans]])

    first = prune_orphan_vectors(chunks_conn, fake_vector_store, workspace_id="ws", max_delete=2)
    second = prune_orphan_vectors(chunks_conn, fake_vector_store, workspace_id="ws", max_delete=2)
    third = prune_orphan_vectors(chunks_conn, fake_vector_store, workspace_id="ws", max_delete=2)

    assert (first, second, third) == (2, 2, 1)
    assert set(fake_vector_store.list_ids(NAMESPACE_CHUNKS, workspace_id="ws")) == {"c1"}


def test_prune_missing_chunks_table_is_noop(fake_vector_store) -> None:
    """A DB with no chunks table (legacy / empty) returns 0, never raises."""
    conn = sqlite3.connect(":memory:")
    try:
        fake_vector_store.upsert(NAMESPACE_CHUNKS, [_vrow("orphan1", "ws")])
        assert prune_orphan_vectors(conn, fake_vector_store, workspace_id="ws", max_delete=100) == 0
    finally:
        conn.close()


def test_prune_is_workspace_scoped(chunks_conn, fake_vector_store) -> None:
    """Pruning workspace A never touches workspace B's vectors."""
    _add_chunk(chunks_conn, "a1", "ws-a")
    _add_chunk(chunks_conn, "b1", "ws-b")
    fake_vector_store.upsert(
        NAMESPACE_CHUNKS,
        [_vrow("a1", "ws-a"), _vrow("a-orphan", "ws-a"), _vrow("b-orphan", "ws-b")],
    )

    deleted = prune_orphan_vectors(
        chunks_conn, fake_vector_store, workspace_id="ws-a", max_delete=100
    )

    assert deleted == 1  # only a-orphan
    assert set(fake_vector_store.list_ids(NAMESPACE_CHUNKS, workspace_id="ws-a")) == {"a1"}
    # ws-b's orphan is untouched — pruning ws-a must not cross workspaces.
    assert set(fake_vector_store.list_ids(NAMESPACE_CHUNKS, workspace_id="ws-b")) == {"b-orphan"}


def test_prune_syncs_metadata_row_count(chunks_conn, fake_vector_store) -> None:
    """After a prune the vector_index_metadata.row_count reflects the live count."""
    _create_metadata_table(chunks_conn)
    for cid in ("c1", "c2", "c3"):
        _add_chunk(chunks_conn, cid, "ws")
    fake_vector_store.upsert(
        NAMESPACE_CHUNKS,
        [_vrow(rid, "ws") for rid in ("c1", "c2", "c3", "orphan1", "orphan2")],
    )
    # Deliberately stale cached count — mimics a post-quarantine DB.
    _seed_metadata_row(chunks_conn, "ws", NAMESPACE_CHUNKS, row_count=999)

    deleted = prune_orphan_vectors(
        chunks_conn, fake_vector_store, workspace_id="ws", max_delete=100
    )

    assert deleted == 2
    row = chunks_conn.execute(
        "SELECT row_count FROM vector_index_metadata WHERE workspace_id = 'ws' AND namespace = ?",
        (NAMESPACE_CHUNKS,),
    ).fetchone()
    assert row[0] == 3  # 5 vectors minus 2 orphans deleted


def test_prune_no_orphans_leaves_metadata_untouched(chunks_conn, fake_vector_store) -> None:
    """No orphans → early return → the cached row_count row is not rewritten."""
    _create_metadata_table(chunks_conn)
    for cid in ("c1", "c2"):
        _add_chunk(chunks_conn, cid, "ws")
    fake_vector_store.upsert(NAMESPACE_CHUNKS, [_vrow("c1", "ws"), _vrow("c2", "ws")])
    _seed_metadata_row(chunks_conn, "ws", NAMESPACE_CHUNKS, row_count=2)

    assert (
        prune_orphan_vectors(chunks_conn, fake_vector_store, workspace_id="ws", max_delete=100) == 0
    )
    row = chunks_conn.execute(
        "SELECT row_count, updated_at FROM vector_index_metadata WHERE workspace_id = 'ws'"
    ).fetchone()
    assert row[0] == 2
    assert row[1] == "2020-01-01T00:00:00Z"  # untouched — sync never ran


def test_prune_metadata_table_present_but_no_row(chunks_conn, fake_vector_store) -> None:
    """Metadata table exists but has no row for this namespace → sync is a clean no-op."""
    _create_metadata_table(chunks_conn)  # table created, no row inserted
    _add_chunk(chunks_conn, "c1", "ws")
    fake_vector_store.upsert(NAMESPACE_CHUNKS, [_vrow("c1", "ws"), _vrow("orphan", "ws")])

    assert (
        prune_orphan_vectors(chunks_conn, fake_vector_store, workspace_id="ws", max_delete=100) == 1
    )
    # The prune must not synthesize a partial metadata row.
    count = chunks_conn.execute("SELECT COUNT(*) FROM vector_index_metadata").fetchone()[0]
    assert count == 0


class _MiscountingStore:
    """Vector store wrapper whose ``delete()`` returns a deliberately wrong
    count. Mimics a backend whose ``delete()`` return value is not the true
    rows-removed delta — ``LanceDBStore`` returns the requested id count,
    ``SqliteVecStore`` the real rowcount. ``prune_orphan_vectors`` must
    trust neither: it recomputes from a live ``list_ids``."""

    def __init__(self, inner: VectorStore) -> None:
        self._inner = inner

    def list_ids(self, namespace: str, *, workspace_id: str | None = None) -> list[str]:
        return self._inner.list_ids(namespace, workspace_id=workspace_id)

    def delete(self, namespace: str, ids: list[str]) -> int:
        self._inner.delete(namespace, ids)
        return len(ids) + 100  # grossly, deliberately wrong


def test_prune_row_count_ignores_a_miscounting_delete(chunks_conn, fake_vector_store) -> None:
    """row_count and the pruned-count return are recomputed from a live
    list_ids, never derived from delete()'s return — so a backend whose
    delete() miscounts still leaves an accurate row_count. The pre-fix
    ``len(vector_ids) - deleted`` formula would have written 5 - 102 = -97
    and returned 102."""
    _create_metadata_table(chunks_conn)
    for cid in ("c1", "c2", "c3"):
        _add_chunk(chunks_conn, cid, "ws")
    fake_vector_store.upsert(
        NAMESPACE_CHUNKS,
        [_vrow(rid, "ws") for rid in ("c1", "c2", "c3", "orphan1", "orphan2")],
    )
    _seed_metadata_row(chunks_conn, "ws", NAMESPACE_CHUNKS, row_count=999)

    pruned = prune_orphan_vectors(
        chunks_conn, _MiscountingStore(fake_vector_store), workspace_id="ws", max_delete=100
    )

    assert pruned == 2  # true delta (5 live → 3 live), not the store's bogus 102
    row = chunks_conn.execute(
        "SELECT row_count FROM vector_index_metadata WHERE workspace_id = 'ws' AND namespace = ?",
        (NAMESPACE_CHUNKS,),
    ).fetchone()
    assert row[0] == 3  # 3 live vectors — recomputed from the store
