"""Move 5 — wire-payload helper for decision-neighbor suggestions.

Both /memory/write_decision and /memory/record_with_evidence return a
``decision_neighbors`` field listing the top-N active decisions whose
tokens overlap the new write. This module owns the conversion from
``DecisionNeighbor`` (suggester dataclass) into ``DecisionNeighborPayload``
(pydantic wire type) so neither route file duplicates the loop.

Mirror of ``_capability_suggest_payload`` so both Move-3 and Move-5
hints share the same shape and naming convention.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.schemas.decisions import DecisionNeighborPayload
from agent_memory_lite.ingestion.decision_neighbor_suggester import (
    suggest_decision_neighbors,
)


def decision_neighbor_payloads(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    title: str,
    text: str,
    rationale: str | None = None,
    exclude_id: str | None = None,
    limit: int = 3,
) -> list[DecisionNeighborPayload]:
    """Return the top-N decision-neighbor suggestions as wire payloads."""
    return [
        DecisionNeighborPayload(
            decision_id=n.decision_id,
            title=n.title,
            snippet=n.snippet,
            score=n.score,
            status=n.status,
        )
        for n in suggest_decision_neighbors(
            conn,
            workspace_id=workspace_id,
            title=title,
            text=text,
            rationale=rationale,
            exclude_id=exclude_id,
            limit=limit,
        )
    ]
