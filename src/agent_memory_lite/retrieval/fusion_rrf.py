"""Reciprocal Rank Fusion.

Pure function over multiple ranked lists of candidates. Each list contributes
`1 / (k + rank)` to a candidate's fused score; the constant `k=60` is the
canonical RRF default. Output is sorted by fused score descending.
"""

from __future__ import annotations

from collections.abc import Iterable

from agent_memory_lite.models.retrieval import RetrievalCandidate

DEFAULT_K = 60


def reciprocal_rank_fusion(
    rankings: Iterable[list[RetrievalCandidate]],
    *,
    k: int = DEFAULT_K,
) -> list[tuple[RetrievalCandidate, float, list[str]]]:
    """Fuse multiple ranked lists.

    Returns a list of `(merged_candidate, fused_score, sources)` tuples sorted
    descending by fused score. When the same `id` appears in multiple lists, the
    earliest-seen `RetrievalCandidate` is kept and the source labels are merged.
    """
    if k <= 0:
        raise ValueError("k must be positive")

    scores: dict[str, float] = {}
    seen: dict[str, RetrievalCandidate] = {}
    sources: dict[str, list[str]] = {}
    metadata_by_id: dict[str, dict[str, object]] = {}

    for ranking in rankings:
        for rank, candidate in enumerate(ranking):
            scores[candidate.id] = scores.get(candidate.id, 0.0) + 1.0 / (k + rank + 1)
            if candidate.id not in seen:
                seen[candidate.id] = candidate
                sources[candidate.id] = [candidate.source]
                metadata_by_id[candidate.id] = dict(candidate.metadata)
            elif candidate.source not in sources[candidate.id]:
                sources[candidate.id].append(candidate.source)
            else:
                metadata_by_id[candidate.id].update(candidate.metadata)
                continue
            metadata_by_id[candidate.id].update(candidate.metadata)

    return sorted(
        (
            (seen[i].model_copy(update={"metadata": metadata_by_id[i]}), scores[i], sources[i])
            for i in scores
        ),
        key=lambda triple: triple[1],
        reverse=True,
    )
