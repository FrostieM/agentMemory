"""Phase 2 (skeleton) → Phase 7 (full): ``memory_recall`` MCP handler.

Phase 2 implements the bare-minimum recall: BM25-search the topic to
get seeds, then run spreading_activation 1-2 hops over soft_edges,
return projections ordered by activation. Phase 7 will fold in
causal_links, outcome_floor as a hard filter, and as_of bi-temporal
selection.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.mcp.stdio_guards import _workspace_from_args
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.retrieval.spreading_activation import spread
from agent_memory_lite.storage.reader import get_object, search


def _handle_recall(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args, intent="read")
    topic = str(args.get("topic", "")).strip()
    if not topic:
        return {"hits": [], "seed_ids": [], "depth": 0}
    depth = int(args.get("depth", 2))
    outcome_floor = float(args.get("outcome_floor", -1.0))
    kinds_filter = args.get("kinds") or []
    limit = int(args.get("limit", 10))
    conn = _runtime.db_for(workspace_id)
    # Seed selection: BM25-rank the topic across all kinds, take top 3.
    # Don't log this as a coactivation -- internal seeds, not agent reads.
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
        return {"hits": [], "seed_ids": [], "depth": depth}
    # Spread activation outward from seeds.
    activations = spread(
        conn,
        workspace_id=workspace_id,
        seeds=seeds,
        max_hops=depth,
        max_nodes=limit * 4,
    )
    # Resolve each activated node back to its projection. Apply
    # kind-filter and outcome_floor.
    out: list[dict[str, Any]] = []
    for node in activations:
        if kinds_filter and node.kind not in kinds_filter:
            continue
        proj = get_object(conn, workspace_id=workspace_id, kind=node.kind, object_id=node.object_id)
        if proj is None:
            continue
        if float(proj.get("outcome_score") or 0.0) < outcome_floor:
            continue
        out.append(
            {
                "kind": node.kind,
                "id": node.object_id,
                "activation": round(node.activation, 4),
                "hops": node.hops,
                "projection": proj,
            }
        )
        if len(out) >= limit:
            break
    return {
        "hits": out,
        "seed_ids": [f"{k}:{i}" for k, i, _ in seeds],
        "depth": depth,
    }
