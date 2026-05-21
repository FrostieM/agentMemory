"""Final score combination.

The spec weighting:

    score = 0.30 * semantic
          + 0.25 * keyword
          + 0.15 * graph
          + 0.10 * recency
          + 0.10 * importance
          + 0.10 * confidence
          + 0.05 * feedback_ewma   (v1.4, signed in [-1, 1])
          - stale_penalty
          - conflict_penalty
          - untrusted_penalty

`semantic` and `keyword` were wired in Phase 2. v1.4 wires the remaining
metadata-driven terms: each is read from candidate.metadata and defaults to
0.0 when absent, so flag-off / fixture-light parity is preserved. The
`feedback_ewma` term is the only signed component; values are precomputed by
``retrieval/feedback_aggregator.py`` and stored on the row, so scoring stays
allocation-light per candidate.
"""

from __future__ import annotations

import math

from agent_memory_lite.models.retrieval import RetrievalCandidate, ScoredHit

WEIGHT_SEMANTIC = 0.30
WEIGHT_KEYWORD = 0.25
WEIGHT_GRAPH = 0.15
WEIGHT_RECENCY = 0.10
WEIGHT_IMPORTANCE = 0.10
WEIGHT_CONFIDENCE = 0.10
WEIGHT_FEEDBACK = 0.05


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp into [lo, hi]. Round-2 audit: ``max``/``min`` do NOT
    contain NaN — ``max(0.0, min(1.0, nan))`` returns ``nan``, which
    then poisons the summed ``score`` and makes ``hits.sort`` undefined
    (a single non-finite key scrambles the whole ranking). A corrupt
    distance in LanceDB metadata is enough to trigger it. Treat any
    non-finite input as the floor so the score stays well-ordered."""
    if not math.isfinite(value):
        return lo
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


def _metadata_unit_term(candidate: RetrievalCandidate, key: str) -> float:
    """Read a metadata field that should already live in [0, 1]; clamp on read."""
    raw = candidate.metadata.get(key)
    if not isinstance(raw, int | float):
        return 0.0
    return _clamp(float(raw))


def _metadata_signed_term(candidate: RetrievalCandidate, key: str) -> float:
    """Read a metadata field whose natural range is [-1, 1]."""
    raw = candidate.metadata.get(key)
    if not isinstance(raw, int | float):
        return 0.0
    return _clamp(float(raw), lo=-1.0, hi=1.0)


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
        graph = _metadata_unit_term(candidate, "graph_score")
        recency = _metadata_unit_term(candidate, "recency_score")
        importance = _metadata_unit_term(candidate, "importance")
        confidence = _metadata_unit_term(candidate, "confidence")
        feedback = _metadata_signed_term(candidate, "feedback_ewma")
        rrf_norm = (fused - rrf_min) / rrf_range
        # Use rrf_norm as a multi-source presence boost: a chunk found in both
        # lists out-scores one found in a single list, even if either raw signal
        # is mediocre. Metadata terms default to 0.0 when absent, so candidates
        # without precomputed importance/recency/confidence/feedback are scored
        # exactly as before.
        score = (
            WEIGHT_SEMANTIC * semantic
            + WEIGHT_KEYWORD * keyword
            + WEIGHT_GRAPH * graph
            + WEIGHT_RECENCY * recency
            + WEIGHT_IMPORTANCE * importance
            + WEIGHT_CONFIDENCE * confidence
            + WEIGHT_FEEDBACK * feedback
            + 0.05 * rrf_norm
        )
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
