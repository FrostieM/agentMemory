"""Explain why `memory_get_context` returned a particular context.

Wire-shape dataclasses live in ``explain_models.py``; the
``used_context_objects`` walk and section counts live in
``explain_used_objects.py``. They are re-exported here so existing
imports keep working.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.models.retrieval import RetrievalCandidate, RetrievalQuery
from agent_memory_lite.repositories.behavior_repo import list_suppressed_behavior_instructions
from agent_memory_lite.retrieval.candidates_fts import collect_fts
from agent_memory_lite.retrieval.candidates_vector import collect_vector
from agent_memory_lite.retrieval.context_builder import build_context, filter_context_hits
from agent_memory_lite.retrieval.context_builder_constants import MAX_FTS_HITS, MAX_VECTOR_HITS
from agent_memory_lite.retrieval.explain_models import (
    ContextExplanation,
    ScoredCandidateExplanation,
    SourceCandidateExplanation,
    SuppressedBehaviorExplanation,
    UsedContextObjectExplanation,
)
from agent_memory_lite.retrieval.explain_used_objects import (
    section_counts,
    used_context_objects,
)
from agent_memory_lite.retrieval.filters import filter_active
from agent_memory_lite.retrieval.fusion_rrf import reciprocal_rank_fusion
from agent_memory_lite.retrieval.scoring import score_candidates
from agent_memory_lite.utils.tokens import estimate_tokens
from agent_memory_lite.vector_store.base import VectorStore

__all__ = [
    "ContextExplanation",
    "ScoredCandidateExplanation",
    "SourceCandidateExplanation",
    "SuppressedBehaviorExplanation",
    "UsedContextObjectExplanation",
    "explain_context",
    "used_context_objects",
]


def _source_explanations(
    rankings: list[list[RetrievalCandidate]],
) -> list[SourceCandidateExplanation]:
    out: list[SourceCandidateExplanation] = []
    for ranking in rankings:
        for rank, candidate in enumerate(ranking, start=1):
            out.append(
                SourceCandidateExplanation(
                    id=candidate.id,
                    source=candidate.source,
                    rank=rank,
                    raw_score=candidate.raw_score,
                    path=candidate.path,
                    metadata=candidate.metadata,
                )
            )
    return out


def _collect_rankings(
    conn: sqlite3.Connection,
    query: RetrievalQuery,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> list[list[RetrievalCandidate]]:
    rankings: list[list[RetrievalCandidate]] = []
    fts = collect_fts(conn, workspace_id=query.workspace_id, query=query.query, limit=MAX_FTS_HITS)
    if fts:
        rankings.append(fts)
    if embedding_provider is not None and vector_store is not None:
        vector = collect_vector(
            conn,
            vector_store,
            embedding_provider,
            workspace_id=query.workspace_id,
            query=query.query,
            limit=MAX_VECTOR_HITS,
        )
        if vector:
            rankings.append(vector)
    return rankings


def explain_context(
    conn: sqlite3.Connection,
    query: RetrievalQuery,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
    candidate_limit: int = 30,
) -> ContextExplanation:
    rankings = _collect_rankings(conn, query, embedding_provider, vector_store)
    scored = filter_active(
        score_candidates(reciprocal_rank_fusion(rankings)),
        historical=query.historical,
    )
    quality_filtered = filter_context_hits(scored, historical=query.historical)
    quality_ids = {hit.id for hit in quality_filtered}
    built = build_context(
        conn, query, embedding_provider=embedding_provider, vector_store=vector_store
    )
    included_ids = [hit.id for hit in built.hits]
    included = set(included_ids)
    suppressed_behavior = [
        SuppressedBehaviorExplanation(
            id=item.id, name=item.name, reason=item.reason, details=item.details
        )
        for item in list_suppressed_behavior_instructions(
            conn, workspace_id=query.workspace_id, query=query.query, limit=20
        )
    ]
    scored_explanations = [
        ScoredCandidateExplanation(
            id=hit.id,
            score=hit.score,
            sources=hit.sources,
            included=hit.id in included,
            reason=(
                "included_in_context"
                if hit.id in included
                else (
                    "suppressed_low_quality_or_stale"
                    if hit.id not in quality_ids
                    else "not_in_final_context_budget_or_rank"
                )
            ),
            path=hit.path,
            metadata=hit.metadata,
        )
        for hit in scored[:candidate_limit]
    ]
    return ContextExplanation(
        workspace_id=query.workspace_id,
        query=query.query,
        max_tokens=query.max_tokens,
        context_tokens=estimate_tokens(built.text),
        section_counts=section_counts(built),
        source_candidates=_source_explanations(rankings)[:candidate_limit],
        scored_candidates=scored_explanations,
        included_ids=included_ids,
        used_context_objects=used_context_objects(built),
        suppressed_behavior_instructions=suppressed_behavior,
    )
