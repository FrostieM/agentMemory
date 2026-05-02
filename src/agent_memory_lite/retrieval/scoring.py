"""Final score combination.

The spec weighting:

    score = 0.30 * semantic
          + 0.25 * keyword
          + 0.15 * graph
          + 0.10 * recency
          + 0.10 * importance
          + 0.10 * confidence
          - stale_penalty
          - conflict_penalty
          - untrusted_penalty

Phase 2 wires `semantic` (cosine similarity, clamped to [0, 1]) and `keyword`
(rank-normalised FTS presence). The remaining components default to neutral
(0.0) and will be set when graph + temporal data lands in Phase 4.
"""

from __future__ import annotations

from agent_memory_lite.models.retrieval import RetrievalCandidate, ScoredHit

WEIGHT_SEMANTIC = 0.30
WEIGHT_KEYWORD = 0.25
WEIGHT_GRAPH = 0.15
WEIGHT_RECENCY = 0.10
WEIGHT_IMPORTANCE = 0.10
WEIGHT_CONFIDENCE = 0.10


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _semantic_term(candidate: RetrievalCandidate, sources: list[str]) -> float:
    if "vector" not in sources:
        return 0.0
    raw_score = candidate.metadata.get("vector_score")
    if not isinstance(raw_score, int | float):
        raw_score = candidate.raw_score if candidate.source == "vector" else 0.0
    return _clamp((float(raw_score) + 1.0) / 2.0)


def _keyword_term(candidate: RetrievalCandidate, sources: list[str]) -> float:
    if "fts" not in sources:
        return 0.0
    rank = candidate.metadata.get("fts_rank")
    if isinstance(rank, int) and rank >= 0:
        return 1.0 / (rank + 1.0)
    # Fallback for older candidates/tests that do not carry fts_rank. Do not use
    # BM25 magnitude here: SQLite FTS can return large negative values for very
    # strong exact matches, so magnitude-based scoring buries the best hit.
    return 1.0


def score_candidates(
    triples: list[tuple[RetrievalCandidate, float, list[str]]],
) -> list[ScoredHit]:
    if not triples:
        return []
    rrf_scores = [t[1] for t in triples]
    rrf_min = min(rrf_scores)
    rrf_max = max(rrf_scores)
    rrf_range = rrf_max - rrf_min or 1e-9

    hits: list[ScoredHit] = []
    for candidate, fused, sources in triples:
        semantic = _semantic_term(candidate, sources)
        keyword = _keyword_term(candidate, sources)
        rrf_norm = (fused - rrf_min) / rrf_range
        # Use rrf_norm as a multi-source presence boost: a chunk found in both
        # lists out-scores one found in a single list, even if either raw signal
        # is mediocre.
        score = WEIGHT_SEMANTIC * semantic + WEIGHT_KEYWORD * keyword + 0.05 * rrf_norm
        hits.append(
            ScoredHit(
                id=candidate.id,
                workspace_id=candidate.workspace_id,
                text=candidate.text,
                path=candidate.path,
                summary=candidate.summary,
                score=score,
                sources=sources,
                metadata=candidate.metadata,
            )
        )
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits
