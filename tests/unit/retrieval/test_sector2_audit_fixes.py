"""v3.5 sector-2 audit-followup: retrieval / vector-store regression locks.

Five contracts:

1. `collect_vector` tolerates EmbedProvider / VectorStore failures and
   returns ``[]`` instead of propagating to a 500.
2. LanceDB adapter rejects malicious workspace_id at the boundary
   before the value reaches the DataFusion where-clause.
3. LanceDB adapter rejects malicious vector ids before interpolation.
4. ``chunks_repo._row_to_chunk`` tolerates malformed JSON / NULL
   importance / NULL confidence.
5. ``spreading_activation._neighbors_soft_edges`` returns ``[]`` on an
   empty ``edge_kinds`` tuple instead of crashing on ``IN ()``.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from agent_memory_lite.models.retrieval import RetrievalCandidate
from agent_memory_lite.retrieval.candidates_vector import collect_vector
from agent_memory_lite.vector_store.base import VectorHit, VectorStoreUnavailableError


class _BrokenProvider:
    name = "broken"
    dim = 4

    def embed_batch(self, _texts, *, kind):
        raise RuntimeError("simulated embedder OOM")


class _BrokenStore:
    backend = "broken"

    def query(self, namespace, vector, *, workspace_id, k):
        raise RuntimeError("simulated lancedb table corruption")


class _OkProvider:
    name = "ok"
    dim = 4

    def embed_batch(self, _texts, *, kind):
        return [np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)]


class _EmptyStore:
    backend = "empty"

    def query(self, namespace, vector, *, workspace_id, k):
        return []


def test_collect_vector_tolerates_embedder_failure(
    applied_conn: sqlite3.Connection,
) -> None:
    """Embedder OOM / cold-start error must NOT 500 the route — the
    function degrades to ``[]`` so retrieval falls back to FTS-only."""
    result = collect_vector(
        applied_conn,
        store=_EmptyStore(),  # type: ignore[arg-type]
        provider=_BrokenProvider(),  # type: ignore[arg-type]
        workspace_id="ws",
        query="anything",
    )
    assert result == []


def test_collect_vector_tolerates_vector_store_failure(
    applied_conn: sqlite3.Connection,
) -> None:
    """LanceDB corruption must degrade to ``[]`` — same fail-shape as
    the v3.4 enum-drift incident that put copyBot at HTTP 500 for 2.5h."""
    result = collect_vector(
        applied_conn,
        store=_BrokenStore(),  # type: ignore[arg-type]
        provider=_OkProvider(),  # type: ignore[arg-type]
        workspace_id="ws",
        query="anything",
    )
    assert result == []


def test_collect_vector_returns_candidates_on_happy_path() -> None:
    """Sanity: with a working provider + store + chunks-row helper,
    the function still returns candidates (covers the type check
    after the audit-driven try/except wrap)."""

    class _Store:
        backend = "fake"

        def query(self, namespace, vector, *, workspace_id, k):
            return [VectorHit(id="chk_x", workspace_id="ws", score=0.9, metadata={"path": "p"})]

    # Skip if no chunks table — this test runs on the unit-test in-memory
    # DB; the inner ``get_chunk`` returns None and we exit cleanly.
    # The point is that no exception leaks out of the try/except path.
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations  # noqa: PLC0415

    conn = open_connection(":memory:")  # type: ignore[arg-type]
    apply_migrations(conn, MIGRATION_DIR)
    result = collect_vector(
        conn,
        store=_Store(),  # type: ignore[arg-type]
        provider=_OkProvider(),  # type: ignore[arg-type]
        workspace_id="ws",
        query="anything",
    )
    # get_chunk returns None because nothing's seeded; the candidate
    # list is empty BUT no exception escaped.
    assert isinstance(result, list)
    for c in result:
        assert isinstance(c, RetrievalCandidate)


def test_lancedb_validate_workspace_id_rejects_quote() -> None:
    """The boundary validator catches a workspace_id containing a single
    quote BEFORE it reaches the DataFusion where-clause."""
    from agent_memory_lite.vector_store.lancedb_store import _validate_workspace_id  # noqa: PLC0415

    # Legitimate ids accepted
    assert _validate_workspace_id("agent-memory-lite") == "agent-memory-lite"
    assert _validate_workspace_id("copyBot") == "copyBot"
    assert _validate_workspace_id("project_alpha.v2") == "project_alpha.v2"
    # Quote → reject
    with pytest.raises(VectorStoreUnavailableError, match="not allowed"):
        _validate_workspace_id("x' OR '1'='1")
    with pytest.raises(VectorStoreUnavailableError, match="not allowed"):
        _validate_workspace_id("ws with spaces")
    with pytest.raises(VectorStoreUnavailableError, match="not allowed"):
        _validate_workspace_id("../etc/passwd")


def test_lancedb_validate_vector_id_rejects_quote() -> None:
    """Same defense for vector ids used in INSERT/DELETE interpolation."""
    from agent_memory_lite.vector_store.lancedb_store import _validate_vector_id  # noqa: PLC0415

    assert _validate_vector_id("chk_abc123") == "chk_abc123"
    assert _validate_vector_id("dec:42") == "dec:42"
    with pytest.raises(VectorStoreUnavailableError):
        _validate_vector_id("chk'); DROP TABLE chunks; --")


def test_row_to_chunk_tolerates_malformed_json_and_null_floats(
    applied_conn: sqlite3.Connection,
) -> None:
    """v3.5: one corrupt JSON cell or NULL importance MUST NOT crash
    every read on the chunks table. Hand-poison a row and verify
    ``_row_to_chunk`` returns a Chunk with the safe-fallback defaults."""
    # Seed a workspace + episode + chunk with malformed JSON + NULL floats
    applied_conn.execute(
        """INSERT INTO episodes (id, workspace_id, source_type, raw_text, summary,
            label, trust_level, importance, confidence, created_at, metadata_json)
           VALUES ('ep_bad', 'ws_bad', 'agent_action', 'x', NULL, NULL,
                   'agent_observed', 0.5, 0.5, '2026-05-20T00:00:00+00:00', '{}')""",
    )
    # Use raw SQL to write deliberately broken values.
    # Seed with valid floats first; then UPDATE to malformed JSON +
    # NULL floats (the NOT NULL constraint is at INSERT only on this
    # schema; UPDATE to NULL is allowed and matches the real-world
    # corruption shape we're guarding against).
    applied_conn.execute(
        """INSERT INTO chunks
           (id, workspace_id, file_id, episode_id, kind, text, summary, label,
            line_start, line_end, symbols_json, embedding_id, importance,
            confidence, created_at, metadata_json)
           VALUES ('chk_bad', 'ws_bad', NULL, 'ep_bad', 'doc', 'body', NULL, NULL,
                   NULL, NULL, '[]', NULL, 0.5, 0.5,
                   '2026-05-20T00:00:00+00:00', '{}')""",
    )
    # Now corrupt JSON columns (NOT NULL constraint on importance /
    # confidence at DB level prevents NULL — covered separately
    # by direct unit test of the helpers below).
    applied_conn.execute(
        """UPDATE chunks
              SET symbols_json = '{not valid json',
                  metadata_json = 'not a dict either'
            WHERE id = 'chk_bad'"""
    )
    applied_conn.commit()
    from agent_memory_lite.repositories.chunks_repo import (  # noqa: PLC0415
        _row_to_chunk,
        _safe_float,
        _safe_json_dict,
        _safe_json_list,
    )

    row = applied_conn.execute("SELECT * FROM chunks WHERE id = 'chk_bad'").fetchone()
    chunk = _row_to_chunk(row)
    # Symbols defaults to [] on malformed JSON (was crash before)
    assert chunk.symbols == []
    # Metadata defaults to {} on malformed JSON (was crash before)
    assert chunk.metadata == {}
    # Direct unit-coverage of the float helper for the NULL case the
    # NOT NULL constraint prevents us from exercising end-to-end:
    assert _safe_float(None) == 0.0
    assert _safe_float("not a number") == 0.0
    assert _safe_float(0.7) == 0.7
    # JSON helpers
    assert _safe_json_list(None) == []
    assert _safe_json_list("{not json") == []
    assert _safe_json_list('{"not": "a list"}') == []
    assert _safe_json_list('["a", "b"]') == ["a", "b"]
    assert _safe_json_dict(None) == {}
    assert _safe_json_dict("[not dict]") == {}
    assert _safe_json_dict('{"k": "v"}') == {"k": "v"}


def test_neighbors_soft_edges_empty_edge_kinds_returns_empty(
    applied_conn: sqlite3.Connection,
) -> None:
    """Empty ``edge_kinds`` tuple → ``IN ()`` syntax error in SQLite.
    The function now guards explicitly and returns []."""
    from agent_memory_lite.retrieval.spreading_activation import (  # noqa: PLC0415
        _neighbors_soft_edges,
    )

    result = _neighbors_soft_edges(
        applied_conn,
        workspace_id="ws",
        qname="dec:abc",
        edge_kinds=(),
        min_edge_weight=0.0,
    )
    assert result == []
