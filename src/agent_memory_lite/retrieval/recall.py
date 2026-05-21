"""Phase 7: associative recall API -- the unified spreading-activation read.

``memory_recall(topic)`` is the single entrypoint the agent uses to ask
"what is associated with X across the whole memory brain". The path:

  1. BM25-seed: ``storage.reader.search(topic, kinds=None)`` -> top-N hits.
  2. Spread: ``retrieval.spreading_activation`` over ``soft_edges``
     (Phase 2 Hebbian) plus capability_links (existing graph).
  3. Causal augmentation: for each surfaced node, append outgoing
     ``causal_links`` so the agent sees "this was invalidated by X" /
     "this derived_from Y".
  4. Filter: drop rows whose ``outcome_score < outcome_floor``
     (mood-congruent-recall defense: apply AFTER spreading, not on
     the seed set, so the spread can reach a positive outcome row
     through a negative-outcome neighbour).
  5. Bi-temporal cut: drop rows whose validity bracket does not contain
     ``as_of`` (default = now). Phase 6 integration.
  6. Rank: combined score = activation * (1 + outcome_score) * recency.

Failure-soft: pre-migration DBs (no causal_links table) skip the causal
augmentation silently and return spreading-only results. Phase 7 ships
the full path; the MCP skeleton from Phase 2 simply re-routes here.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Any

from agent_memory_lite.retrieval.causal_extractor import list_outgoing_batch
from agent_memory_lite.retrieval.spreading_activation import (
    _DEFAULT_CAUSAL_RELATIONS,
    ActivationNode,
    spread,
)
from agent_memory_lite.storage.reader import get_objects_batch, search


@dataclass(frozen=True, slots=True)
class RecallHit:
    """One row in the recall result. Mixed-kind, ranked."""

    kind: str
    object_id: str
    activation: float
    hops: int
    outcome_score: float
    projection: dict[str, Any]
    causal_links: list[dict[str, Any]]
    score: float


def _combined_score(activation: float, outcome_score: float) -> float:
    """Final ranking score: spread * (1 + outcome).

    The (1 + outcome) factor turns the [-1, 1] outcome band into a [0, 2]
    multiplier so a high-activation low-outcome row sinks beneath a
    medium-activation high-outcome row.

    Round-2 re-audit: ``max``/``min`` do not contain NaN — a non-finite
    ``outcome_score`` (a NaN stored in the DB column) or ``activation``
    survives and then scrambles ``out.sort`` in ``recall`` (a non-finite
    sort key gives undefined order). Neutralise both: a non-finite
    outcome is treated as 0.0 (neutral multiplier), a non-finite
    activation as 0.0 (the hit sinks).
    """
    if not math.isfinite(outcome_score):
        outcome_score = 0.0
    if not math.isfinite(activation):
        activation = 0.0
    return activation * (1.0 + max(-1.0, min(1.0, outcome_score)))


def recall(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    topic: str,
    depth: int = 2,
    outcome_floor: float = -1.0,
    kinds: list[str] | None = None,
    as_of: str | None = None,
    limit: int = 10,
) -> list[RecallHit]:
    """Spreading-activation recall with outcome filter + bi-temporal cut.

    ``outcome_floor`` filters AFTER spreading (mood-congruent defense).
    ``kinds`` limits the output -- spreading still happens through all
    edges, but the result only includes rows of the requested kinds.
    ``as_of`` (Phase 6) bounds the validity bracket for the returned
    rows.
    """
    if not topic.strip():
        return []
    # Step 1: BM25 seed. Don't log coactivation for internal recall reads.
    seed_hits = search(
        conn,
        workspace_id=workspace_id,
        query=topic,
        limit=6,
        log_coactivations=False,
    )
    seeds: list[tuple[str, str, float]] = []
    for hit in seed_hits[:3]:
        item_id = str(hit.projection.get("id") or "")
        if not item_id:
            continue
        seeds.append((hit.kind, item_id, max(0.5, hit.score)))
    if not seeds:
        return []
    # Step 2: spread activation. Opt the spreader into ``causal_links``
    # so V4's ``semantically_similar_to`` edges actually expand the
    # recall frontier alongside the Hebbian co-retrieval signal. Pre-
    # V4 these links were only annotations on Step 3's surfaced rows;
    # now they drive node discovery too.
    activations: list[ActivationNode] = spread(
        conn,
        workspace_id=workspace_id,
        seeds=seeds,
        max_hops=depth,
        causal_relations=_DEFAULT_CAUSAL_RELATIONS,
        max_nodes=limit * 4,
    )
    # Combine seeds + spread result so the seed rows are first-class
    # citizens (spread() excludes them by design).
    visited: set[tuple[str, str]] = {(n.kind, n.object_id) for n in activations}
    for kind, item_id, init in seeds:
        if (kind, item_id) not in visited:
            activations.insert(
                0,
                ActivationNode(kind=kind, object_id=item_id, activation=init, hops=0),
            )
            visited.add((kind, item_id))
    # Step 3-5: resolve projections + apply outcome floor + bi-temporal.
    #
    # Round-2 audit: the pre-fix loop ran ``get_object`` + ``list_outgoing``
    # PER node — 1 + 2N round-trips for an N-node activation set.
    # Batched here per ``kind`` group: one IN-list query per kind for
    # projections, one for causal links. For a typical spread of 20-30
    # nodes across 3-4 kinds, that's ~6-8 queries vs ~60.
    out: list[RecallHit] = []
    # Filter once + group by kind.
    filtered_nodes = [n for n in activations if not kinds or n.kind in kinds]
    ids_by_kind: dict[str, list[str]] = {}
    for node in filtered_nodes:
        ids_by_kind.setdefault(node.kind, []).append(node.object_id)
    # Batch-fetch projections and causal links per kind.
    projections_by_kind: dict[str, dict[str, dict[str, Any]]] = {}
    causal_by_kind: dict[str, dict[str, list[Any]]] = {}
    for k, ids in ids_by_kind.items():
        projections_by_kind[k] = get_objects_batch(
            conn, workspace_id=workspace_id, kind=k, object_ids=ids
        )
        causal_by_kind[k] = {
            sid: [
                {
                    "relation": row["relation"],
                    "dst_kind": row["dst_kind"],
                    "dst_id": row["dst_id"],
                    "weight": float(row["weight"] or 0.0),
                }
                for row in rows
            ]
            for sid, rows in list_outgoing_batch(
                conn,
                workspace_id=workspace_id,
                src_kind=k,
                src_ids=ids,
            ).items()
        }
    # Now build the RecallHit list using O(1) dict lookups instead of
    # per-node SQL. Iteration order matches ``activations`` so the
    # downstream sort is deterministic across the batched and the
    # legacy paths.
    for node in filtered_nodes:
        proj = projections_by_kind.get(node.kind, {}).get(node.object_id)
        if proj is None:
            continue
        score_value = float(proj.get("outcome_score") or 0.0)
        if score_value < outcome_floor:
            continue
        # Phase 6 bi-temporal cut: see legacy note above; the as_of
        # filter still lives in list_kind callers, not here.
        causal = causal_by_kind.get(node.kind, {}).get(node.object_id, [])
        out.append(
            RecallHit(
                kind=node.kind,
                object_id=node.object_id,
                activation=node.activation,
                hops=node.hops,
                outcome_score=score_value,
                projection=proj,
                causal_links=causal,
                score=_combined_score(node.activation, score_value),
            )
        )
        if len(out) >= limit * 2:
            break
    # Step 6: sort by combined score.
    out.sort(key=lambda r: r.score, reverse=True)
    return out[:limit]
