"""Per-workspace Hebbian distillation core.

Houses the pair-enumeration routine that turns one workspace's
``retrieval_coactivation`` log into ``soft_edges`` upserts, plus the
HeLa-Mem outcome-score lookup that gates which pairs are strengthened.
Extracted from ``hebbian_pass`` to keep that orchestration module small;
the public symbols are re-exported from ``hebbian_pass`` so the import
surface is unchanged.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.repositories.soft_edges_repo import upsert_soft_edge

# Kinds whose outcome_score column lives in their own table (Phase 1).
# The HeLa-Mem gate looks up these scores; unknown kinds default to 0.
_KIND_TABLE: dict[str, str] = {
    "decision": "decisions",
    "theory": "theories",
    "behavior": "behaviors",
    "skill": "skills",
    "insight": "insights",
    "chunk": "chunks",
}


def qualified(kind: str, item_id: str) -> str:
    """Synthetic qualified name for a memory row in the soft_edges table."""
    return f"{kind}:{item_id}"


def _outcome_score(conn: sqlite3.Connection, kind: str, item_id: str) -> float:
    """Look up outcome_score for one row. Returns 0.0 when missing."""
    table = _KIND_TABLE.get(kind)
    if not table:
        return 0.0
    try:
        row = conn.execute(f"SELECT outcome_score FROM {table} WHERE id = ?", (item_id,)).fetchone()
    except sqlite3.OperationalError:
        return 0.0
    if row is None:
        return 0.0
    return float(row[0] or 0.0)


def distill_workspace(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    outcome_gate: bool = True,
    min_group_size: int = 2,
) -> tuple[int, int]:
    """Distill one workspace's coactivation log into soft_edges.

    Returns (edges_upserted, edges_skipped_by_gate).
    """
    try:
        rows = conn.execute(
            "SELECT query_hash, item_kind, item_id, rank FROM retrieval_coactivation "
            "WHERE workspace_id = ? ORDER BY query_hash, rank",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return 0, 0
    if not rows:
        return 0, 0
    # Group by query_hash.
    groups: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault(row["query_hash"], []).append(row)
    upserted = 0
    gated = 0
    # Round-2 audit (M3): the i<j pair loop below is O(N^2) in the group
    # size. A single query that logged 500 co-activations would
    # enumerate ~125k upsert_soft_edge calls in one synchronous pass on
    # a background thread (long lock hold / DoS). ``items`` is already
    # rank-ordered by the SELECT, so cap to the top-N most-relevant
    # co-activations per group — bounds the pair count at N*(N-1)/2.
    max_group_items = 20
    for items in groups.values():
        if len(items) < min_group_size:
            continue
        capped = items[:max_group_items]
        for i in range(len(capped)):
            for j in range(i + 1, len(capped)):
                a, b = capped[i], capped[j]
                if a["item_kind"] == b["item_kind"] and a["item_id"] == b["item_id"]:
                    continue
                if outcome_gate:
                    score_a = _outcome_score(conn, a["item_kind"], a["item_id"])
                    score_b = _outcome_score(conn, b["item_kind"], b["item_id"])
                    # HeLa-Mem gate: skip ONLY when BOTH sides are strictly
                    # negative. Earlier version used ``<= 0`` which also
                    # banned neutral [0, 0] pairs and effectively froze the
                    # graph on fresh workspaces (every decision starts at
                    # outcome_score=0 until feedback propagates). Empirical
                    # probe on copyBot (250 decisions, all outcome=0)
                    # surfaced this regression: 4 pairs, all gated, zero
                    # edges. The new gate only blocks pairs rooted in
                    # demonstrated failure -- neutral pairs accumulate
                    # normally so the Hebbian graph can bootstrap.
                    if score_a < 0.0 and score_b < 0.0:
                        gated += 1
                        continue
                rank_a = max(1, int(a["rank"] or 1))
                rank_b = max(1, int(b["rank"] or 1))
                increment = 1.0 / float(rank_a * rank_b)
                src = qualified(a["item_kind"], a["item_id"])
                dst = qualified(b["item_kind"], b["item_id"])
                # Order-stable: store the smaller qualified name as src so
                # we don't double-count the same undirected pair.
                if src > dst:
                    src, dst = dst, src
                try:
                    upsert_soft_edge(
                        conn,
                        workspace_id=workspace_id,
                        src=src,
                        dst=dst,
                        kind="co_retrieved",
                        weight_increment=increment,
                    )
                    upserted += 1
                except (sqlite3.Error, ValueError):
                    continue
    conn.commit()
    return upserted, gated
