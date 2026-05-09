"""1.7.0: soft-edge co-change accumulator for the file ingest pipeline.

When a file is ingested with NEW versions for ≥2 of its symbols
(detected via the ``versions_written > 0`` signal that the
post-chunk phase already produces), every pair (X, Y) of changed
symbols receives a ``co_changed`` weight increment. This builds a
heuristic "these symbols evolve together" graph that complements
the explicit hard graph.

Pair-stable ordering: emit edges in both directions so neighbor
queries from either symbol work, but use the same weight increment
on both sides (the relation is symmetric for ``co_changed``).

O(N²) per file. Bounded by the chunk count per file — typical files
have <50 symbols, so the worst case is ~2500 upserts per ingest,
which SQLite handles in single-digit milliseconds.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.repositories.soft_edges_repo import upsert_soft_edge


def record_co_change_pairs(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    changed_qnames: list[str],
    increment: float = 1.0,
) -> int:
    """Upsert ``co_changed`` soft edges for every ordered pair in
    ``changed_qnames``. Returns the count of pair upserts performed.
    """
    if len(changed_qnames) < 2:
        return 0
    written = 0
    for i, src in enumerate(changed_qnames):
        for j, dst in enumerate(changed_qnames):
            if i == j or not src or not dst:
                continue
            upsert_soft_edge(
                conn,
                workspace_id=workspace_id,
                src=src,
                dst=dst,
                kind="co_changed",
                weight_increment=increment,
            )
            written += 1
    return written
