"""Handlers for v3.1 active-memory MCP tools.

Thin shape-conversion layer over the maintenance/ scanners. Same
``available`` flag semantics as the HTTP routes so an operator can
introspect missing-schema state from either surface.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.mcp.stdio_guards import _workspace_from_args
from agent_memory_lite.mcp.stdio_runtime import _runtime

_TRUE_STRINGS: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def _coerce_bool(raw: object, *, default: bool = False) -> bool:
    """Audit M5-fix: tolerate string 'true' / 'false' from MCP wire layer.

    Some MCP clients pass JSON booleans as strings; ``bool('false')`` is
    ``True``, which would silently flip a write side-effect.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in _TRUE_STRINGS
    return default


def _coerce_limit(raw: object, *, hi: int = 50) -> int | None:
    """Audit M4-fix: clamp ``limit`` to [1, hi] to match HTTP route.

    The MCP wire schema declares maximum: 50 but the handler must
    enforce it because the schema is advisory only.
    """
    if raw is None:
        return None
    try:
        v: int = int(raw)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
    return max(1, min(hi, v))


def _handle_propose_experiments(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args, intent="read")
    limit = _coerce_limit(args.get("limit"))
    persist = _coerce_bool(args.get("persist"), default=False)
    conn = _runtime.db_for(workspace_id)
    from agent_memory_lite.maintenance.experiment_proposal import (  # noqa: PLC0415
        find_proposal_candidates,
        is_enabled,
        persist_proposal,
    )

    enabled = is_enabled()
    try:
        proposals = find_proposal_candidates(conn, workspace_id=workspace_id, limit=limit)
        available = True
    except sqlite3.OperationalError:
        proposals = []
        available = False
    candidate_ids: list[str] = []
    if persist and available and proposals:
        try:
            conn.execute("BEGIN IMMEDIATE")
            for proposal in proposals:
                candidate_id = persist_proposal(conn, workspace_id=workspace_id, proposal=proposal)
                if candidate_id:
                    candidate_ids.append(candidate_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {
        "workspace_id": workspace_id,
        "proposals": [
            {
                "insight_id": p.insight_id,
                "proposal_text": p.proposal_text,
                "validation_criterion": p.validation_criterion,
                "confidence": p.confidence,
                "source_episode_id": p.source_episode_id,
            }
            for p in proposals
        ],
        "persisted": len(candidate_ids),
        "candidate_ids": candidate_ids,
        "feature_enabled": enabled,
        "available": available,
    }


def _handle_predictive_warnings(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args, intent="read")
    limit = _coerce_limit(args.get("limit"))
    conn = _runtime.db_for(workspace_id)
    from agent_memory_lite.maintenance.predictive_failure import (  # noqa: PLC0415
        find_predictive_warnings,
        is_enabled,
    )

    enabled = is_enabled()
    try:
        warnings = find_predictive_warnings(conn, workspace_id=workspace_id, limit=limit)
        available = True
    except sqlite3.OperationalError:
        warnings = []
        available = False
    return {
        "workspace_id": workspace_id,
        "warnings": [
            {
                "fresh_decision_id": w.fresh_decision_id,
                "failed_decision_id": w.failed_decision_id,
                "similarity": w.similarity,
                "reason_text": w.reason_text,
            }
            for w in warnings
        ],
        "feature_enabled": enabled,
        "available": available,
    }
