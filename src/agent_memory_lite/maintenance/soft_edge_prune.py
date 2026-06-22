"""Value-based prune for the ``soft_edges`` code-graph (the #1 table by rows).

``soft_edges`` holds two populations:

* MEMORY edges (``co_retrieved`` / ``co_mentioned``) -- ``decision:id``-shaped
  associations distilled from the co-retrieval log. ``spreading_activation``
  walks these for ``memory_recall`` and the brief's associates section. NEVER
  pruned here.
* CODE edges (``co_changed`` / ``similar_signature`` / ``co_referenced``) --
  bare code-symbol pairs written by ingestion. They are read-dead: ``spread()``
  is only ever seeded with memory rows and the graph is bipartite (a memory seed
  never reaches a bare code symbol), ``impact_check`` reads ``symbol_edges`` (not
  ``soft_edges``), and ``list_soft_neighbors`` has no caller. So a
  single-observation code edge is pure storage bloat -- ~66% of the table, which
  grows ~O(N^2) with the indexed symbol count.

This prunes ONLY single-observation, stale CODE edges:
``edge_kind`` in the code set AND ``observation_count <= max_observation_count``
AND ``last_seen_at < cutoff``. The staleness gate avoids churning an edge that
ingestion is actively reinforcing (``upsert_soft_edge`` bumps
``observation_count`` + ``last_seen_at``); a reinforced (>= 2) edge is kept. If a
pruned pair recurs, ingestion simply re-inserts it. Self-contained, bounded, and
failure-soft -- like the other data-deleting brain steps.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from agent_memory_lite.utils.time import parse_iso

# Code-graph edge kinds (bare-symbol endpoints, written by ingestion). The
# MEMORY edge kinds (co_retrieved / co_mentioned) are deliberately excluded so
# the recall + brief associative graph is never touched.
_CODE_EDGE_KINDS = ("co_changed", "co_referenced", "similar_signature")


@dataclass
class SoftEdgePruneReport:
    """Outcome of one soft-edge prune pass for a workspace."""

    pruned: int = 0
    error: str = ""


def _cutoff(now_iso: str, days: int) -> str:
    """ISO cutoff = now - days. Returns "" on a bad timestamp, which makes the
    ``last_seen_at < ?`` predicate match nothing (a safe no-op, never a mass
    delete)."""
    try:
        return (parse_iso(now_iso) - timedelta(days=max(0, days))).isoformat()
    except (TypeError, ValueError):
        return ""


def prune_soft_edges(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    max_observation_count: int,
    min_age_days: int,
    cap: int,
    now_iso: str,
) -> SoftEdgePruneReport:
    """Bounded prune of single-observation, stale CODE edges. Failure-soft.

    NEVER touches ``co_retrieved`` / ``co_mentioned`` (the memory/Hebbian edges
    feeding recall + the brief). Bounded to ``cap`` rows per pass via
    ``id IN (SELECT id ... LIMIT ?)`` so a large backlog drains over several
    passes instead of one giant transaction. The ``edge_kind`` set and all
    predicates are in-module constants -- never caller/agent input.
    """
    report = SoftEdgePruneReport()
    cutoff = _cutoff(now_iso, min_age_days)
    if not cutoff:
        return report  # bad timestamp -> safe no-op
    placeholders = ", ".join("?" for _ in _CODE_EDGE_KINDS)
    try:
        cur = conn.execute(
            f"""
            DELETE FROM soft_edges WHERE id IN (
                SELECT id FROM soft_edges
                 WHERE workspace_id = ?
                   AND edge_kind IN ({placeholders})
                   AND observation_count <= ?
                   AND last_seen_at < ?
                 LIMIT ?
            )
            """,
            (workspace_id, *_CODE_EDGE_KINDS, max_observation_count, cutoff, cap),
        )
        report.pruned = cur.rowcount or 0
        conn.commit()
    except sqlite3.Error as exc:
        report.error = str(exc)
    return report
