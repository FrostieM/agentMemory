"""LanceDBStore.compact() -- collapses MVCC version history without data loss.

Every upsert appends a manifest version; nothing else prunes them (measured
4282 versions / 340 MB for 7356 live rows). compact() runs Table.optimize.
"""

from __future__ import annotations

import numpy as np

from agent_memory_lite.vector_store.base import VectorRow
from agent_memory_lite.vector_store.lancedb_store import LanceDBStore


def _rows(n: int) -> list[VectorRow]:
    return [
        VectorRow(
            id=f"id{i}",
            workspace_id="ws",
            vector=np.ones(8, dtype=np.float32),
            metadata={},
        )
        for i in range(n)
    ]


def test_compact_preserves_live_rows(tmp_path) -> None:
    store = LanceDBStore(tmp_path / "v.lance")
    try:
        store.upsert("chunks", _rows(3))
        for _ in range(4):  # churn -> many superseded versions
            store.upsert("chunks", _rows(3))
        result = store.compact(cleanup_older_than_seconds=0)
        # All live rows survive compaction (latest version is never removed).
        assert result.get("chunks") == 3
        hits = store.query("chunks", np.ones(8, dtype=np.float32), workspace_id="ws", k=5)
        assert len(hits) == 3  # vectors still queryable post-compaction
    finally:
        store.close()


def test_compact_empty_store_is_noop(tmp_path) -> None:
    store = LanceDBStore(tmp_path / "empty.lance")
    try:
        assert store.compact() == {}  # no tables -> empty result, no error
    finally:
        store.close()
