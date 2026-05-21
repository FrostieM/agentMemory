"""Paired unit test for the orphan-vector prune brain step."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import numpy as np
import pytest

from agent_memory_lite.maintenance.vector_prune import prune_orphan_vectors
from agent_memory_lite.vector_store.base import VectorRow
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
