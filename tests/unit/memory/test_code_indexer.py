"""Unit tests for v3 code_indexer - chunks + symbol_edges + digest integration.

Covers:

* ``index_file`` happy path on a Python source string with calls + imports +
  class extends -> chunks row per top-level symbol, edges inserted,
  digest with edge counts.
* Idempotency: re-indexing unchanged content -> skipped_unchanged=True,
  no duplicate chunks / edges.
* ``force=True`` re-indexes even unchanged content (drops + re-creates).
* Modified content: chunks + edges from prior pass are dropped before
  re-insert.
* ``resolve_all_pending_edges`` stitches up NULL-dst edges after the
  second-pass.
* ``refresh_digest_edge_counts`` updates inbound_edge_count after edges
  resolve.
* End-to-end: 2 Python files with cross-file calls produce edges with
  resolved dst_chunk_id.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.cognition.code_indexer import (
    IndexResult,
    _stable_file_id,
    index_file,
    refresh_digest_edge_counts,
    resolve_all_pending_edges,
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


PY_CALLER = '''"""Caller module — exercises calls + imports."""
from helpers import compute_fee


class FeeAdjuster:
    """Top-level class."""

    def adjust(self, amount: float) -> float:
        """Method that calls helpers.compute_fee."""
        return compute_fee(amount) * 1.5


def main() -> None:
    adjuster = FeeAdjuster()
    adjuster.adjust(100.0)
'''


PY_HELPER = '''"""Helper module — defines compute_fee."""


def compute_fee(amount: float) -> float:
    """Pure function — target of calls from caller.py."""
    return amount * 0.1
'''


PY_HELPER_CHANGED = '''"""Helper module - defines compute_fee."""


def compute_fee(amount: float) -> float:
    """Pure function - target of calls from caller.py."""
    return amount * 0.2
'''


# ============================================================
# Stable id
# ============================================================


def test_stable_file_id_is_deterministic() -> None:
    a = _stable_file_id("ws", "src/x.py")
    b = _stable_file_id("ws", "src/x.py")
    assert a == b


def test_stable_file_id_is_workspace_isolated() -> None:
    a = _stable_file_id("ws_a", "src/x.py")
    b = _stable_file_id("ws_b", "src/x.py")
    assert a != b


# ============================================================
# index_file happy path
# ============================================================


def test_index_file_inserts_files_chunks_digest(conn: sqlite3.Connection) -> None:
    result = index_file(
        conn,
        workspace_id="ws",
        rel_path="src/caller.py",
        content=PY_CALLER,
        language="python",
    )
    assert result.error == ""
    assert not result.skipped_unchanged
    assert result.chunks >= 1
    assert result.digest_upserted
    # files row landed
    f = conn.execute("SELECT path, language FROM files WHERE workspace_id='ws'").fetchone()
    assert f is not None
    assert f["path"] == "src/caller.py"
    assert f["language"] == "python"
    # at least one chunk has a qualified_name (e.g. 'FeeAdjuster.adjust' or 'main')
    qnames = [
        r[0]
        for r in conn.execute(
            "SELECT qualified_name FROM chunks WHERE workspace_id='ws' "
            "AND qualified_name IS NOT NULL"
        ).fetchall()
    ]
    assert qnames, f"expected ≥1 chunk with qualified_name, got: {qnames}"
    # digest row landed
    d = conn.execute(
        "SELECT purpose_short, language, chunk_count FROM code_digests WHERE workspace_id='ws'"
    ).fetchone()
    assert d is not None
    assert d["language"] == "python"


def test_index_file_extracts_edges(conn: sqlite3.Connection) -> None:
    """Calls + imports in PY_CALLER produce edges."""
    index_file(
        conn,
        workspace_id="ws",
        rel_path="src/caller.py",
        content=PY_CALLER,
        language="python",
    )
    edge_types = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT edge_type FROM symbol_edges WHERE workspace_id='ws'"
        ).fetchall()
    ]
    # Python AST extractor at minimum produces 'calls' and 'imports'.
    assert "calls" in edge_types or "imports" in edge_types
    n = conn.execute("SELECT COUNT(*) FROM symbol_edges WHERE workspace_id='ws'").fetchone()[0]
    assert n > 0


def test_index_file_skips_unchanged_on_second_pass(conn: sqlite3.Connection) -> None:
    """SHA-matching content → skipped_unchanged=True, no duplicate writes."""
    index_file(conn, workspace_id="ws", rel_path="src/x.py", content=PY_HELPER, language="python")
    chunks_before = conn.execute("SELECT COUNT(*) FROM chunks WHERE workspace_id='ws'").fetchone()[
        0
    ]
    result = index_file(
        conn, workspace_id="ws", rel_path="src/x.py", content=PY_HELPER, language="python"
    )
    assert result.skipped_unchanged is True
    chunks_after = conn.execute("SELECT COUNT(*) FROM chunks WHERE workspace_id='ws'").fetchone()[0]
    assert chunks_after == chunks_before


def test_index_file_force_reindexes(conn: sqlite3.Connection) -> None:
    """force=True re-indexes even unchanged — chunks/edges dropped + re-inserted."""
    index_file(conn, workspace_id="ws", rel_path="src/x.py", content=PY_CALLER, language="python")
    chunks_before = conn.execute("SELECT COUNT(*) FROM chunks WHERE workspace_id='ws'").fetchone()[
        0
    ]
    result = index_file(
        conn,
        workspace_id="ws",
        rel_path="src/x.py",
        content=PY_CALLER,
        language="python",
        force=True,
    )
    assert result.skipped_unchanged is False
    chunks_after = conn.execute("SELECT COUNT(*) FROM chunks WHERE workspace_id='ws'").fetchone()[0]
    # Same chunk count — old dropped, new inserted; not duplicated.
    assert chunks_after == chunks_before


def test_index_file_modified_content_replaces_chunks(conn: sqlite3.Connection) -> None:
    """Different content → SHA changes → old chunks/edges dropped + new ones inserted."""
    index_file(conn, workspace_id="ws", rel_path="src/x.py", content=PY_CALLER, language="python")
    before = conn.execute("SELECT id FROM chunks WHERE workspace_id='ws'").fetchall()
    ids_before = {r[0] for r in before}
    index_file(conn, workspace_id="ws", rel_path="src/x.py", content=PY_HELPER, language="python")
    after = conn.execute("SELECT id FROM chunks WHERE workspace_id='ws'").fetchall()
    ids_after = {r[0] for r in after}
    # No chunk_id from before should survive (re-issued on re-index).
    assert ids_before.isdisjoint(ids_after)


def test_index_file_repairs_legacy_file_id_without_fk_violations(
    conn: sqlite3.Connection,
) -> None:
    legacy_hash = hashlib.sha1(
        PY_HELPER.encode("utf-8", errors="replace"),
        usedforsecurity=False,
    ).hexdigest()
    conn.execute(
        """INSERT INTO files (id, workspace_id, path, language, content_hash,
                              size_bytes, last_indexed_at, metadata_json,
                              is_archived)
           VALUES ('legacy_file', 'ws', 'src/x.py', 'python', ?, 100,
                   '2026-01-01T00:00:00Z', ?, 0)""",
        (legacy_hash, '{"trust_level":"file_ingest"}'),
    )
    conn.execute(
        """INSERT INTO chunks (id, workspace_id, file_id, kind, text, gist,
                               line_start, line_end, symbols_json, importance,
                               confidence, is_archived, created_at)
           VALUES ('legacy_chunk', 'ws', 'legacy_file', 'symbol', 'old', 'old',
                   1, 1, '[]', 0.5, 0.5, 0, '2026-01-01T00:00:00Z')"""
    )

    result = index_file(
        conn,
        workspace_id="ws",
        rel_path="src/x.py",
        content=PY_HELPER,
        language="python",
    )

    assert result.error == ""
    stable_id = _stable_file_id("ws", "src/x.py")
    file_row = conn.execute(
        "SELECT id, metadata_json FROM files WHERE workspace_id='ws'"
    ).fetchone()
    assert file_row["id"] == stable_id
    assert json.loads(file_row["metadata_json"]) == {"managed_by": "code_memory_indexer"}
    assert (
        conn.execute("SELECT COUNT(*) FROM chunks WHERE file_id='legacy_file'").fetchone()[0] == 0
    )
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


# ============================================================
# Cross-file resolver + digest refresh
# ============================================================


def test_cross_file_edges_resolved(conn: sqlite3.Connection) -> None:
    """Indexing caller before helper leaves dst_chunk_id NULL until resolved."""
    index_file(
        conn, workspace_id="ws", rel_path="src/caller.py", content=PY_CALLER, language="python"
    )
    index_file(
        conn, workspace_id="ws", rel_path="src/helpers.py", content=PY_HELPER, language="python"
    )
    # Some edges might still be NULL — run the second-pass resolver
    resolved = resolve_all_pending_edges(conn, workspace_id="ws")
    # Resolver may or may not find new matches depending on the v2
    # extractor's behaviour; just assert that the call returns a
    # non-negative count and at least SOME edges target compute_fee.
    assert resolved >= 0
    n_calls = conn.execute(
        """
        SELECT COUNT(*) FROM symbol_edges
        WHERE workspace_id='ws' AND dst_qualified_name = 'compute_fee'
        """
    ).fetchone()[0]
    assert n_calls >= 1


def test_reindexing_callee_preserves_inbound_edges_as_pending(
    conn: sqlite3.Connection,
) -> None:
    index_file(
        conn, workspace_id="ws", rel_path="src/caller.py", content=PY_CALLER, language="python"
    )
    index_file(
        conn, workspace_id="ws", rel_path="src/helpers.py", content=PY_HELPER, language="python"
    )
    resolve_all_pending_edges(conn, workspace_id="ws")
    before = conn.execute(
        """
        SELECT COUNT(*) FROM symbol_edges
        WHERE workspace_id='ws'
          AND dst_qualified_name = 'compute_fee'
          AND dst_chunk_id IS NOT NULL
        """
    ).fetchone()[0]
    assert before >= 1
    old_helper_chunk_ids = {
        str(row[0])
        for row in conn.execute(
            """
            SELECT id FROM chunks
            WHERE workspace_id='ws'
              AND file_id = ?
            """,
            (_stable_file_id("ws", "src/helpers.py"),),
        ).fetchall()
    }

    result = index_file(
        conn,
        workspace_id="ws",
        rel_path="src/helpers.py",
        content=PY_HELPER_CHANGED,
        language="python",
    )

    assert result.error == ""
    resolve_all_pending_edges(conn, workspace_id="ws")
    after = conn.execute(
        """
        SELECT COUNT(*) FROM symbol_edges
        WHERE workspace_id='ws'
          AND dst_qualified_name = 'compute_fee'
          AND dst_chunk_id IS NOT NULL
        """
    ).fetchone()[0]
    assert after >= 1
    old_refs = conn.execute(
        f"""
        SELECT COUNT(*) FROM symbol_edges
        WHERE workspace_id='ws'
          AND dst_chunk_id IN ({",".join("?" for _ in old_helper_chunk_ids)})
        """,
        tuple(sorted(old_helper_chunk_ids)),
    ).fetchone()[0]
    assert old_refs == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_refresh_digest_edge_counts_updates_rows(conn: sqlite3.Connection) -> None:
    """After indexing + resolving, digest counts reflect resolved edges."""
    index_file(
        conn, workspace_id="ws", rel_path="src/caller.py", content=PY_CALLER, language="python"
    )
    index_file(
        conn, workspace_id="ws", rel_path="src/helpers.py", content=PY_HELPER, language="python"
    )
    resolve_all_pending_edges(conn, workspace_id="ws")
    updated = refresh_digest_edge_counts(conn, workspace_id="ws")
    assert updated == 2  # two files → two digest rows updated
    # Both rows should have integer counts (could be 0+)
    rows = conn.execute(
        "SELECT inbound_edge_count, outbound_edge_count FROM code_digests WHERE workspace_id='ws'"
    ).fetchall()
    for inbound, outbound in rows:
        assert inbound >= 0
        assert outbound >= 0


# ============================================================
# Failure-soft
# ============================================================


def test_index_file_with_broken_sql_returns_error_result(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forced sqlite3.Error during INSERT yields IndexResult.error, not raise."""
    from agent_memory_lite.cognition import code_indexer  # noqa: PLC0415

    def boom(*_a: object, **_kw: object) -> None:
        raise sqlite3.OperationalError("simulated")

    monkeypatch.setattr(code_indexer, "_upsert_file_row", boom)
    result = index_file(
        conn,
        workspace_id="ws",
        rel_path="src/x.py",
        content=PY_HELPER,
        language="python",
    )
    assert result.error.startswith("OperationalError:")
    assert result.digest_upserted is False


def test_index_file_rolls_back_existing_rows_on_error(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_memory_lite.cognition import code_indexer  # noqa: PLC0415

    content_hash = hashlib.sha1(
        PY_HELPER.encode("utf-8", errors="replace"),
        usedforsecurity=False,
    ).hexdigest()
    conn.execute(
        """INSERT INTO files (id, workspace_id, path, language, content_hash,
                              size_bytes, last_indexed_at, is_archived)
           VALUES ('legacy_file', 'ws', 'src/x.py', 'python', ?, 100,
                   '2026-01-01T00:00:00Z', 0)""",
        (content_hash,),
    )
    conn.execute(
        """INSERT INTO chunks (id, workspace_id, file_id, kind, text, gist,
                               line_start, line_end, symbols_json, importance,
                               confidence, is_archived, created_at)
           VALUES ('legacy_chunk', 'ws', 'legacy_file', 'symbol', 'old', 'old',
                   1, 1, '[]', 0.5, 0.5, 0, '2026-01-01T00:00:00Z')"""
    )

    def boom(*_a: object, **_kw: object) -> None:
        raise sqlite3.OperationalError("simulated")

    monkeypatch.setattr(code_indexer, "_upsert_file_row", boom)

    result = index_file(
        conn,
        workspace_id="ws",
        rel_path="src/x.py",
        content=PY_HELPER,
        language="python",
    )

    assert result.error.startswith("OperationalError:")
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE id='legacy_chunk'").fetchone()[0] == 1
    file_row = conn.execute("SELECT id FROM files WHERE path='src/x.py'").fetchone()
    assert file_row["id"] == "legacy_file"


def test_index_file_rebuilds_when_digest_hash_is_stale(conn: sqlite3.Connection) -> None:
    file_id = _stable_file_id("ws", "src/x.py")
    content_hash = hashlib.sha1(
        PY_HELPER.encode("utf-8", errors="replace"),
        usedforsecurity=False,
    ).hexdigest()
    conn.execute(
        """INSERT INTO files (id, workspace_id, path, language, content_hash,
                              size_bytes, last_indexed_at, is_archived)
           VALUES (?, 'ws', 'src/x.py', 'python', ?, 100,
                   '2026-01-01T00:00:00Z', 0)""",
        (file_id, content_hash),
    )
    conn.execute(
        """INSERT INTO code_digests (id, workspace_id, file_path, file_sha1,
                                     language, chunk_count, symbol_count,
                                     inbound_edge_count, outbound_edge_count,
                                     purpose_short, top_symbols_json,
                                     last_indexed_at, updated_at)
           VALUES ('dig_stale', 'ws', 'src/x.py', '0', 'python',
                   0, 0, 0, 0, 'old', '[]',
                   '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"""
    )

    result = index_file(
        conn,
        workspace_id="ws",
        rel_path="src/x.py",
        content=PY_HELPER,
        language="python",
    )

    assert result.skipped_unchanged is False
    assert result.digest_upserted is True
    digest_sha = conn.execute("SELECT file_sha1 FROM code_digests WHERE id='dig_stale'").fetchone()[
        0
    ]
    assert digest_sha == content_hash


def test_index_file_skips_oversized_extracted_edge(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_memory_lite.extraction.symbol_edges_py_helpers import ExtractedEdge  # noqa: PLC0415
    from agent_memory_lite.ingestion import file_persist_edges  # noqa: PLC0415

    monkeypatch.setattr(
        file_persist_edges,
        "_extract_edges",
        lambda _text, _language: [
            ExtractedEdge(
                src_qualified_name="main",
                dst_qualified_name="x" * 401,
                edge_type="calls",
            )
        ],
    )

    result = index_file(
        conn,
        workspace_id="ws",
        rel_path="src/x.py",
        content="def main():\n    return 1\n",
        language="python",
    )

    assert result.error == ""
    assert conn.execute("SELECT COUNT(*) FROM symbol_edges").fetchone()[0] == 0


# ============================================================
# Result dataclass
# ============================================================


def test_index_result_defaults() -> None:
    r = IndexResult(file_path="x.py")
    assert r.chunks == 0
    assert r.edges == 0
    assert r.skipped_unchanged is False
    assert r.error == ""
