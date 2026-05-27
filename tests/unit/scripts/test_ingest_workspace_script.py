from __future__ import annotations

import sqlite3

from scripts.ingest_workspace import prune_stale_files


class _FakeVectorStore:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, namespace: str, ids: list[str]) -> int:
        assert namespace == "chunks"
        self.deleted.extend(ids)
        return len(ids)


def test_prune_stale_files_deletes_db_and_vector_rows() -> None:
    conn = sqlite3.connect(":memory:")
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(conn)
    conn.execute(
        """INSERT INTO files (id, workspace_id, path, language, content_hash,
                              size_bytes, last_indexed_at, is_archived)
           VALUES ('file_stale', 'ws', 'src/deleted.py', 'python', 'old', 10,
                   '2026-01-01T00:00:00Z', 0)"""
    )
    conn.execute(
        """INSERT INTO chunks (id, workspace_id, file_id, kind, text, gist,
                               line_start, line_end, symbols_json, importance,
                               confidence, is_archived, created_at)
           VALUES ('chk_stale', 'ws', 'file_stale', 'symbol', 'old', 'old',
                   1, 1, '[]', 0.5, 0.5, 0, '2026-01-01T00:00:00Z')"""
    )
    conn.execute(
        """INSERT INTO chunks_fts (chunk_id, workspace_id, path, symbols, text, summary)
           VALUES ('chk_stale', 'ws', 'src/deleted.py', '', 'old', '')"""
    )
    conn.execute(
        """INSERT INTO symbol_edges (id, workspace_id, src_chunk_id,
                                     src_qualified_name, dst_qualified_name,
                                     dst_chunk_id, edge_type, src_language,
                                     created_at)
           VALUES ('edge_stale', 'ws', 'chk_stale', 'old.use', 'old.target',
                   'chk_stale', 'calls', 'python', '2026-01-01T00:00:00Z')"""
    )
    conn.commit()
    store = _FakeVectorStore()

    counts = prune_stale_files(
        conn,
        workspace_id="ws",
        expected_paths={"src/live.py"},
        vector_store=store,
    )

    assert counts == {"files": 1, "chunks": 1, "edges": 1, "vectors": 1}
    assert store.deleted == ["chk_stale"]
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM symbol_edges").fetchone()[0] == 0


def test_prune_stale_files_allows_deferred_vector_rebuild() -> None:
    conn = sqlite3.connect(":memory:")
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(conn)
    conn.execute(
        """INSERT INTO files (id, workspace_id, path, language, content_hash,
                              size_bytes, last_indexed_at, is_archived)
           VALUES ('file_stale', 'ws', 'src/deleted.py', 'python', 'old', 10,
                   '2026-01-01T00:00:00Z', 0)"""
    )
    conn.execute(
        """INSERT INTO chunks (id, workspace_id, file_id, kind, text, gist,
                               line_start, line_end, symbols_json, importance,
                               confidence, is_archived, created_at)
           VALUES ('chk_stale', 'ws', 'file_stale', 'symbol', 'old', 'old',
                   1, 1, '[]', 0.5, 0.5, 0, '2026-01-01T00:00:00Z')"""
    )
    conn.commit()

    counts = prune_stale_files(
        conn,
        workspace_id="ws",
        expected_paths=set(),
        vector_store=None,
    )

    assert counts == {"files": 1, "chunks": 1, "edges": 0, "vectors": 0}
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
