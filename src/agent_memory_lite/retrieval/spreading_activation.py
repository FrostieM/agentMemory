"""Phase 2: spreading activation — read-side of the Hebbian graph.

Walk ``soft_edges`` (the cross-kind associative graph) outward from a
set of seeds, accumulating activation along weighted edges. Pure BFS,
bounded by ``max_hops`` and an inhibitory cutoff.

Activation law:
    activation[v] = sum_over_edges_from_visited(weight(u, v) * activation[u] * 0.5^hops)

That is: each hop halves the contribution, and weak edges
(``weight < min_edge_weight``) are pruned so noise can't accumulate.
The 0.5^hops decay is the same law spreading activation theory uses in
the connectionist literature and matches the brief's recency curve.

Seeds are ``(kind, id, score)`` tuples; outputs are the same shape.
Phase 7's ``memory_recall`` calls this directly. Phase 2's brief
``_build_associates`` uses ``depth=1`` for cheap neighbor lookup.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from dataclasses import dataclass

# Default to memory-row associations only. Caller can pass
# ``edge_kinds=None`` (default) to include all kinds, or restrict to
# code-graph edges by listing only ``co_changed`` / ``co_referenced`` /
# ``similar_signature``.
_DEFAULT_EDGE_KINDS = (
    "co_retrieved",
    "co_mentioned",
    "co_changed",
    "co_referenced",
    "similar_signature",
)

# v3.1 Vector 4 follow-up: spreading_activation walks causal_links too
# when a caller opts in via ``causal_relations``. The relation set
# below is the safe full default for the recall path — it includes the
# semantic-similarity links emitted by ``retrieval.causal_embedding``
# (the 190-link layer V4 ships) plus the operator-derived
# (``derived_from`` / ``supersedes`` / ``invalidated_by`` /
# ``contradicts``) family, so V4 actually drives recall expansion
# instead of just annotating the surfaced rows.
_DEFAULT_CAUSAL_RELATIONS = (
    "semantically_similar_to",
    "derived_from",
    "supersedes",
    "invalidated_by",
    "contradicts",
    # v3.3: DiD-extracted causal links from supersede pairs with a
    # meaningful outcome delta. See ``retrieval.causal_did``.
    "caused",
)


@dataclass(frozen=True, slots=True)
class ActivationNode:
    """One node in the activation result, qualified-name format."""

    kind: str
    object_id: str
    activation: float
    hops: int

    @property
    def qualified(self) -> str:
        return f"{self.kind}:{self.object_id}"


def _split_qualified(qname: str) -> tuple[str, str]:
    """Split ``"<kind>:<id>"`` into ``(kind, id)``. Returns ``("", qname)``
    when the string is a bare code-symbol qualified name (no colon)."""
    if ":" not in qname:
        return "", qname
    kind, item_id = qname.split(":", 1)
    return kind, item_id


def _neighbors_soft_edges(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    qname: str,
    edge_kinds: tuple[str, ...],
    min_edge_weight: float,
) -> list[tuple[str, float]]:
    """Return ``(neighbor_qualified_name, edge_weight)`` from ``soft_edges``.

    Soft edges are undirected in spirit but stored directionally; we
    UNION both endpoints so a seed reaches associates whether it was
    the src or dst at insert time.
    """
    placeholders = ", ".join("?" * len(edge_kinds))
    try:
        rows = conn.execute(
            f"""
            SELECT dst_qualified_name AS other, weight FROM soft_edges
             WHERE workspace_id = ? AND src_qualified_name = ?
               AND edge_kind IN ({placeholders}) AND weight >= ?
            UNION ALL
            SELECT src_qualified_name AS other, weight FROM soft_edges
             WHERE workspace_id = ? AND dst_qualified_name = ?
               AND edge_kind IN ({placeholders}) AND weight >= ?
            """,
            (
                workspace_id,
                qname,
                *edge_kinds,
                min_edge_weight,
                workspace_id,
                qname,
                *edge_kinds,
                min_edge_weight,
            ),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[tuple[str, float]] = []
    for row in rows:
        out.append((row["other"], float(row["weight"] or 0.0)))
    return out


def _neighbors_causal(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    qname: str,
    causal_relations: tuple[str, ...],
    min_edge_weight: float,
) -> list[tuple[str, float]]:
    """Return ``(neighbor_qualified_name, edge_weight)`` from ``causal_links``.

    ``causal_links`` stores endpoints as separate ``(kind, id)`` columns
    rather than the qualified-string form ``soft_edges`` uses, so we
    rebuild the qualified name via ``kind || ':' || id`` and parse the
    incoming ``qname`` the same way. Bidirectional UNION mirrors the
    ``soft_edges`` traversal — outgoing links surface the dst, incoming
    links surface the src, so V4's ``semantically_similar_to`` rows
    (which are stored as ``earlier -> later``) are reachable from either
    endpoint.

    Wrapped in its own try/except so a pre-migration DB that has
    ``soft_edges`` but not ``causal_links`` keeps spreading on soft
    edges instead of going dark.
    """
    src_kind, src_id = _split_qualified(qname)
    if not src_kind or not src_id:
        return []
    placeholders = ", ".join("?" * len(causal_relations))
    try:
        rows = conn.execute(
            f"""
            SELECT (dst_kind || ':' || dst_id) AS other, weight
              FROM causal_links
             WHERE workspace_id = ? AND src_kind = ? AND src_id = ?
               AND relation IN ({placeholders}) AND weight >= ?
            UNION ALL
            SELECT (src_kind || ':' || src_id) AS other, weight
              FROM causal_links
             WHERE workspace_id = ? AND dst_kind = ? AND dst_id = ?
               AND relation IN ({placeholders}) AND weight >= ?
            """,
            (
                workspace_id,
                src_kind,
                src_id,
                *causal_relations,
                min_edge_weight,
                workspace_id,
                src_kind,
                src_id,
                *causal_relations,
                min_edge_weight,
            ),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[tuple[str, float]] = []
    for row in rows:
        out.append((row["other"], float(row["weight"] or 0.0)))
    return out


def _neighbors(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    qname: str,
    edge_kinds: tuple[str, ...],
    min_edge_weight: float,
    causal_relations: tuple[str, ...] | None = None,
) -> list[tuple[str, float]]:
    """Combine ``soft_edges`` and (optional) ``causal_links`` neighbours.

    De-duplicates ``(other, weight)`` pairs but does NOT sum duplicate
    weights — if the same pair shows up on both substrates, the
    spread() body still walks each row, so the natural reinforcement
    from showing up twice falls out of the BFS contribution sum. The
    dedup here is only to avoid the literal-duplicate row from the
    bidirectional UNION inside one substrate.
    """
    soft = _neighbors_soft_edges(
        conn,
        workspace_id=workspace_id,
        qname=qname,
        edge_kinds=edge_kinds,
        min_edge_weight=min_edge_weight,
    )
    causal: list[tuple[str, float]] = []
    if causal_relations:
        causal = _neighbors_causal(
            conn,
            workspace_id=workspace_id,
            qname=qname,
            causal_relations=causal_relations,
            min_edge_weight=min_edge_weight,
        )
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for other, weight in (*soft, *causal):
        if other in seen or other == qname:
            continue
        seen.add(other)
        out.append((other, weight))
    return out


def spread(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    seeds: list[tuple[str, str, float]],
    max_hops: int = 2,
    decay: float = 0.5,
    min_edge_weight: float = 0.1,
    min_activation: float = 0.05,
    edge_kinds: tuple[str, ...] | None = None,
    causal_relations: tuple[str, ...] | None = None,
    max_nodes: int = 64,
) -> list[ActivationNode]:
    """BFS activation spread from ``seeds`` to depth ``max_hops``.

    ``seeds`` is a list of ``(kind, id, initial_activation)``. Returns
    every activated node EXCEPT the seeds themselves, sorted by
    activation desc. The inhibitory cutoff ``min_edge_weight`` matches
    the standard 0.1 floor; ``min_activation`` prunes deep low-impact
    nodes so the result set stays bounded.

    ``causal_relations`` opts the traversal into ``causal_links``
    alongside ``soft_edges``. Pass ``_DEFAULT_CAUSAL_RELATIONS`` (or a
    subset) to let V4's ``semantically_similar_to`` rows drive spread
    expansion in addition to the Hebbian co-retrieval signal. Default
    ``None`` keeps backwards-compatible behaviour for callers (e.g.
    the brief's ``_build_associates``) that don't want causal nodes
    surfacing as cheap one-hop neighbours.
    """
    if not seeds or max_hops <= 0:
        return []
    kinds = edge_kinds if edge_kinds is not None else _DEFAULT_EDGE_KINDS
    activations: dict[str, float] = {}
    hops_seen: dict[str, int] = {}
    seed_qualified: set[str] = set()
    queue: deque[tuple[str, int, float]] = deque()
    for kind, item_id, init in seeds:
        qname = f"{kind}:{item_id}"
        seed_qualified.add(qname)
        activations[qname] = max(activations.get(qname, 0.0), init)
        hops_seen[qname] = 0
        queue.append((qname, 0, init))
    while queue and len(activations) < max_nodes:
        node, hops, current = queue.popleft()
        if hops >= max_hops:
            continue
        neighbours = _neighbors(
            conn,
            workspace_id=workspace_id,
            qname=node,
            edge_kinds=kinds,
            min_edge_weight=min_edge_weight,
            causal_relations=causal_relations,
        )
        for other, weight in neighbours:
            contribution = current * weight * (decay ** (hops + 1))
            if contribution < min_activation:
                continue
            prior = activations.get(other, 0.0)
            new_total = prior + contribution
            activations[other] = new_total
            next_hops = hops + 1
            if hops_seen.get(other, max_hops + 1) > next_hops:
                hops_seen[other] = next_hops
            queue.append((other, next_hops, contribution))
    out: list[ActivationNode] = []
    for qname, total in activations.items():
        if qname in seed_qualified:
            continue
        kind, item_id = _split_qualified(qname)
        if not kind or not item_id:
            continue
        out.append(
            ActivationNode(
                kind=kind,
                object_id=item_id,
                activation=total,
                hops=hops_seen.get(qname, max_hops),
            )
        )
    out.sort(key=lambda n: n.activation, reverse=True)
    return out[:max_nodes]
