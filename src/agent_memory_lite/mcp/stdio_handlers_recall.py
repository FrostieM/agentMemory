"""Phase 7 full implementation: ``memory_recall`` MCP handler.

Delegates to ``retrieval.recall.recall`` for the spreading activation +
causal_links + outcome_floor + bi-temporal pipeline. The handler is a
thin shape-conversion layer; the math lives in ``recall.py``.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.mcp.stdio_guards import _workspace_from_args
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.retrieval.recall import recall


def _handle_recall(args: dict[str, Any]) -> dict[str, Any]:
    # MEMORY_RECALL_ENABLED off-path: byte-equivalent to "feature missing".
    try:
        from agent_memory_lite.config.settings import get_settings  # noqa: PLC0415

        if not get_settings().recall_enabled:
            return {"hits": [], "depth": 0, "disabled": True}
    except Exception:  # pragma: no cover - defensive
        pass
    workspace_id = _workspace_from_args(args, intent="read")
    topic = str(args.get("topic", "")).strip()
    if not topic:
        return {"hits": [], "depth": 0}
    try:
        from agent_memory_lite.config.settings import get_settings  # noqa: PLC0415

        default_depth = get_settings().recall_default_depth
    except Exception:  # pragma: no cover - defensive
        default_depth = 2
    depth = int(args.get("depth", default_depth))
    outcome_floor = float(args.get("outcome_floor", -1.0))
    kinds_filter = args.get("kinds") or []
    limit = int(args.get("limit", 10))
    as_of = args.get("as_of")
    if isinstance(as_of, str):
        as_of = as_of.strip() or None
    conn = _runtime.db_for(workspace_id)
    hits = recall(
        conn,
        workspace_id=workspace_id,
        topic=topic,
        depth=depth,
        outcome_floor=outcome_floor,
        kinds=list(kinds_filter) if kinds_filter else None,
        as_of=as_of if isinstance(as_of, str) else None,
        limit=limit,
    )
    return {
        "topic": topic,
        "depth": depth,
        "hits": [
            {
                "kind": h.kind,
                "id": h.object_id,
                "activation": round(h.activation, 4),
                "hops": h.hops,
                "outcome_score": round(h.outcome_score, 4),
                "score": round(h.score, 4),
                "projection": h.projection,
                "causal_links": h.causal_links,
            }
            for h in hits
        ],
    }
