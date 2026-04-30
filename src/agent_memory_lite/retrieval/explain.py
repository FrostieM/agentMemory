"""Explain why `memory_get_context` returned a particular context."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.models.retrieval import RetrievalCandidate, RetrievalQuery
from agent_memory_lite.repositories.behavior_repo import list_suppressed_behavior_instructions
from agent_memory_lite.retrieval.candidates_fts import collect_fts
from agent_memory_lite.retrieval.candidates_vector import collect_vector
from agent_memory_lite.retrieval.context_builder import (
    MAX_FTS_HITS,
    MAX_VECTOR_HITS,
    build_context,
)
from agent_memory_lite.retrieval.filters import filter_active
from agent_memory_lite.retrieval.fusion_rrf import reciprocal_rank_fusion
from agent_memory_lite.retrieval.scoring import score_candidates
from agent_memory_lite.utils.tokens import estimate_tokens
from agent_memory_lite.vector_store.base import VectorStore


@dataclass(frozen=True, slots=True)
class SourceCandidateExplanation:
    id: str
    source: str
    rank: int
    raw_score: float
    path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScoredCandidateExplanation:
    id: str
    score: float
    sources: list[str]
    included: bool
    reason: str
    path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SuppressedBehaviorExplanation:
    id: str
    name: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContextExplanation:
    workspace_id: str
    query: str
    max_tokens: int
    context_tokens: int
    section_counts: dict[str, int]
    source_candidates: list[SourceCandidateExplanation]
    scored_candidates: list[ScoredCandidateExplanation]
    included_ids: list[str]
    suppressed_behavior_instructions: list[SuppressedBehaviorExplanation] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "query": self.query,
            "max_tokens": self.max_tokens,
            "context_tokens": self.context_tokens,
            "section_counts": self.section_counts,
            "source_candidates": [
                {
                    "id": item.id,
                    "source": item.source,
                    "rank": item.rank,
                    "raw_score": item.raw_score,
                    "path": item.path,
                    "metadata": item.metadata,
                }
                for item in self.source_candidates
            ],
            "scored_candidates": [
                {
                    "id": item.id,
                    "score": item.score,
                    "sources": item.sources,
                    "included": item.included,
                    "reason": item.reason,
                    "path": item.path,
                    "metadata": item.metadata,
                }
                for item in self.scored_candidates
            ],
            "included_ids": self.included_ids,
            "suppressed_behavior_instructions": [
                {
                    "id": item.id,
                    "name": item.name,
                    "reason": item.reason,
                    "details": item.details,
                }
                for item in self.suppressed_behavior_instructions
            ],
        }


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


def _section_counts(context: object) -> dict[str, int]:
    decisions = getattr(context, "decisions", [])
    theories = getattr(context, "theories", [])
    agenda = getattr(context, "research_agenda", None)
    behavior = getattr(context, "behavior_instructions", None)
    capabilities = getattr(context, "agent_capabilities", None)
    return {
        "core": len(getattr(context, "core", [])),
        "behavior_instructions": len(getattr(behavior, "instructions", []) if behavior else []),
        "decisions": len(decisions),
        "theories": len(theories),
        "experiments": len(getattr(agenda, "experiments", []) if agenda else []),
        "insights": len(getattr(agenda, "insights", []) if agenda else []),
        "concepts": len(getattr(agenda, "concepts", []) if agenda else []),
        "snapshots": len(getattr(agenda, "snapshots", []) if agenda else []),
        "roles": len(getattr(capabilities, "roles", []) if capabilities else []),
        "skills": len(getattr(capabilities, "skills", []) if capabilities else []),
        "playbooks": len(getattr(capabilities, "playbooks", []) if capabilities else []),
        "rules": len(getattr(context, "rules", [])),
        "facts": len(getattr(context, "facts", [])),
        "chunks": len(getattr(context, "hits", [])),
    }


def explain_context(
    conn: sqlite3.Connection,
    query: RetrievalQuery,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
    candidate_limit: int = 30,
) -> ContextExplanation:
    rankings: list[list[RetrievalCandidate]] = []
    fts = collect_fts(
        conn,
        workspace_id=query.workspace_id,
        query=query.query,
        limit=MAX_FTS_HITS,
    )
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

    scored = filter_active(
        score_candidates(reciprocal_rank_fusion(rankings)),
        historical=query.historical,
    )
    built = build_context(
        conn,
        query,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    included_ids = [hit.id for hit in built.hits]
    included = set(included_ids)
    suppressed_behavior = [
        SuppressedBehaviorExplanation(
            id=item.id,
            name=item.name,
            reason=item.reason,
            details=item.details,
        )
        for item in list_suppressed_behavior_instructions(
            conn,
            workspace_id=query.workspace_id,
            query=query.query,
            limit=20,
        )
    ]
    scored_explanations = [
        ScoredCandidateExplanation(
            id=hit.id,
            score=hit.score,
            sources=hit.sources,
            included=hit.id in included,
            reason="included_in_context"
            if hit.id in included
            else "not_in_final_context_budget_or_rank",
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
        section_counts=_section_counts(built),
        source_candidates=_source_explanations(rankings)[:candidate_limit],
        scored_candidates=scored_explanations,
        included_ids=included_ids,
        suppressed_behavior_instructions=suppressed_behavior,
    )
