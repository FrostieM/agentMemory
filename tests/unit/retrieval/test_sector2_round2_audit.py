"""Sector 2 Round-2 adversarial audit — regression locks.

A fresh adversarial agent audited the retrieval layer and found:
  #1 search_chunks_fts fed the raw query into FTS5 MATCH (no sanitize)
  #3 lancedb query() json.loads on metadata_json was unguarded
  #4 lancedb upsert() validated row.id but not row.workspace_id
  #5 lancedb reused a table across an embedding-dim change
  #7 scoring._clamp let NaN through and scrambled the ranking sort

This file locks each fix so a re-audit finds nothing.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from agent_memory_lite.retrieval.scoring import _clamp
from agent_memory_lite.vector_store.base import VectorRow, VectorStoreUnavailableError
from agent_memory_lite.vector_store.lancedb_store import _safe_json_loads

# ---------- #1: FTS5 MATCH injection / operator-bomb ----------


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def test_search_chunks_fts_survives_bare_fts_operators(conn: sqlite3.Connection) -> None:
    """A query of bare FTS5 operators (`foo OR`, `"unbalanced`, `NEAR(`)
    used to either silently return zero hits or run an attacker-shaped
    match expression. Post-fix it is sanitized — no crash, no 500."""
    from agent_memory_lite.storage.reader import search_chunks_fts  # noqa: PLC0415

    for hostile in ["foo OR", '"unbalanced', "NEAR(a b)", "a* OR b*", "x AND ("]:
        # Must not raise — sanitizer strips operators before MATCH.
        hits = search_chunks_fts(conn, workspace_id="ws", query=hostile, limit=5)
        assert isinstance(hits, list)


def test_search_chunks_fts_finds_content_despite_operator_token(
    conn: sqlite3.Connection,
) -> None:
    """The real bug: a query containing the word 'OR' (or any operator)
    used to silently return zero hits even when chunks matched. Seed a
    chunk, query with an operator word present, confirm the hit still
    surfaces."""
    from agent_memory_lite.storage.reader import search_chunks_fts  # noqa: PLC0415

    conn.execute(
        """INSERT INTO chunks (id, workspace_id, kind, text, created_at)
           VALUES ('ch1', 'ws', 'episode', 'kelly sizing OR variance', '2026-05-20')"""
    )
    conn.execute(
        """INSERT INTO chunks_fts (chunk_id, workspace_id, path, text, summary)
           VALUES ('ch1', 'ws', '', 'kelly sizing OR variance', '')"""
    )
    conn.commit()
    hits = search_chunks_fts(conn, workspace_id="ws", query="kelly OR sizing", limit=5)
    # 'OR' is stripped; 'kelly' + 'sizing' AND-match the seeded chunk.
    assert len(hits) == 1
    assert hits[0].projection["id"] == "ch1"


# ---------- #3: metadata_json decode guard ----------


def test_safe_json_loads_tolerates_corrupt_metadata() -> None:
    """One corrupt metadata_json cell must not break the whole query."""
    assert _safe_json_loads('{"a": 1}') == {"a": 1}
    assert _safe_json_loads("{truncated") == {}
    assert _safe_json_loads("") == {}
    assert _safe_json_loads(None) == {}
    # A valid JSON value that isn't an object → empty dict (the column
    # contract is "object or nothing").
    assert _safe_json_loads("[1,2,3]") == {}
    assert _safe_json_loads('"a string"') == {}


# ---------- #4: upsert workspace_id validation ----------


def test_upsert_rejects_unsafe_workspace_id(tmp_path: Path) -> None:
    """upsert validated row.id but not row.workspace_id — a quote in
    workspace_id used to be STORED, creating orphan vectors. Now it is
    rejected at the write boundary."""
    from agent_memory_lite.vector_store.lancedb_store import LanceDBStore  # noqa: PLC0415

    store = LanceDBStore(tmp_path / "vec.lance")
    bad = VectorRow(
        id="v1",
        workspace_id="ws' OR '1'='1",
        vector=np.array([0.1, 0.2, 0.3], dtype=np.float32),
        metadata={},
    )
    with pytest.raises(VectorStoreUnavailableError, match="allow-list"):
        store.upsert("chunks", [bad])
    store.close()


# ---------- #5: embedding dimension mismatch ----------


def test_open_or_create_rejects_dim_mismatch(tmp_path: Path) -> None:
    """A table built at dim 3 must refuse to reopen for a dim-5 provider
    instead of silently storing mismatched vectors."""
    from agent_memory_lite.vector_store.lancedb_store import LanceDBStore  # noqa: PLC0415

    store = LanceDBStore(tmp_path / "vec.lance")
    store.upsert(
        "chunks",
        [
            VectorRow(
                id="v1",
                workspace_id="ws",
                vector=np.array([0.1, 0.2, 0.3], dtype=np.float32),
                metadata={},
            )
        ],
    )
    # Re-upsert with a 5-dim vector → the embedding model "changed".
    with pytest.raises(VectorStoreUnavailableError, match="reindex_vectors"):
        store.upsert(
            "chunks",
            [
                VectorRow(
                    id="v2",
                    workspace_id="ws",
                    vector=np.array([0.1, 0.2, 0.3, 0.4, 0.5], dtype=np.float32),
                    metadata={},
                )
            ],
        )
    store.close()


# ---------- #7: NaN in scoring._clamp ----------


def test_clamp_neutralizes_non_finite() -> None:
    """NaN / inf must clamp to the floor — a non-finite score key makes
    list.sort produce undefined order, scrambling the whole ranking.
    Every non-finite input collapses to ``lo`` (not ``hi``): inf is not
    a 'large valid score', it is corrupt data, so it sinks."""
    assert _clamp(float("nan")) == 0.0
    assert _clamp(float("inf")) == 0.0
    assert _clamp(float("-inf")) == 0.0
    assert _clamp(float("nan"), lo=-1.0, hi=1.0) == -1.0
    # Finite values still clamp normally.
    assert _clamp(0.5) == 0.5
    assert _clamp(2.0) == 1.0
    assert _clamp(-0.3) == 0.0


def test_clamp_keeps_sort_well_ordered() -> None:
    """Concrete regression: a list with one NaN-derived score must still
    sort deterministically after the fix."""
    scores = [_clamp(x) for x in (0.9, float("nan"), 0.5, float("inf"), 0.1)]
    assert all(math.isfinite(s) for s in scores)
    # Sorting must not raise and must be a total order.
    ordered = sorted(scores, reverse=True)
    assert ordered == sorted(ordered, reverse=True)


# ---------- re-audit follow-up: recall._combined_score NaN ----------


def test_combined_score_neutralizes_non_finite() -> None:
    """Round-2 RE-AUDIT: the _clamp fix targeted scoring.py but missed
    recall._combined_score, which used a raw inline max/min. A non-finite
    outcome_score (a NaN in the DB column) survived and scrambled
    recall's out.sort. Both inputs must now be neutralised."""
    from agent_memory_lite.retrieval.recall import _combined_score  # noqa: PLC0415

    # Every combination of a non-finite input must yield a finite score.
    for activation, outcome in (
        (0.8, float("nan")),
        (0.8, float("inf")),
        (0.8, float("-inf")),
        (float("nan"), 0.5),
        (float("inf"), 0.5),
        (float("nan"), float("nan")),
    ):
        score = _combined_score(activation, outcome)
        assert math.isfinite(score), f"_combined_score({activation},{outcome}) = {score}"
    # Finite inputs still compute normally: 0.8 * (1 + 0.5) == 1.2.
    assert _combined_score(0.8, 0.5) == pytest.approx(1.2)
    # A non-finite outcome collapses to the neutral 0.0 → 0.8 * 1.0.
    assert _combined_score(0.8, float("nan")) == pytest.approx(0.8)
