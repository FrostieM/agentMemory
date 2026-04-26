"""Trust + temporal filters applied to scored hits.

Phase 2 ships pass-through filters; Phase 4 plugs in invalidated-fact filtering
and trust-tier downranking once the graph + decisions layers exist.
"""

from __future__ import annotations

from agent_memory_lite.models.retrieval import ScoredHit


def filter_active(hits: list[ScoredHit], *, historical: bool = False) -> list[ScoredHit]:
    del historical
    return list(hits)
