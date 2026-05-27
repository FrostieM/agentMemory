"""Trust + temporal filters applied to scored hits.

Phase 2 shipped these as pass-through; the archive feature (migration
0016 onward) gives them a real job: drop chunks whose underlying row
was archived, unless the caller asked for ``historical=True``. Other
soft-delete kinds (decisions, theories, instructions, capabilities)
are filtered upstream by their respective list functions.
"""

from __future__ import annotations

from agent_memory_lite.models.retrieval import ScoredHit


def filter_active(hits: list[ScoredHit], *, historical: bool = False) -> list[ScoredHit]:
    if historical:
        return list(hits)
    out: list[ScoredHit] = []
    for hit in hits:
        if bool(hit.metadata.get("is_archived")):
            continue
        out.append(hit)
    return out
