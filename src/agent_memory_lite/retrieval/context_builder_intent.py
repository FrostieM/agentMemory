"""Query intent detection + small diagnostic helpers.

Used by the structured-section fitter to decide which sections must be
preserved when the token budget is tight (e.g. ``research`` queries
should not silently drop the research agenda).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.models.capabilities import AgentCapabilities
from agent_memory_lite.models.research import ResearchAgenda
from agent_memory_lite.models.retrieval import RetrievalCandidate, RetrievalQuery
from agent_memory_lite.retrieval.candidates_fts import collect_fts
from agent_memory_lite.retrieval.candidates_vector import collect_vector
from agent_memory_lite.retrieval.context_builder_constants import (
    INTENT_KEYWORDS,
    MAX_CHUNK_RESERVE_TOKENS,
    MAX_FTS_HITS,
    MAX_VECTOR_HITS,
    MIN_CHUNK_RESERVE_TOKENS,
    RENDER_LEVEL_RANK,
    STRUCTURED_SAFETY_RESERVE_TOKENS,
)
from agent_memory_lite.vector_store.base import VectorStore


def _chunk_reserve_tokens(max_tokens: int, *, has_hits: bool) -> int:
    if not has_hits:
        return min(STRUCTURED_SAFETY_RESERVE_TOKENS, max_tokens)
    return min(MAX_CHUNK_RESERVE_TOKENS, max(MIN_CHUNK_RESERVE_TOKENS, max_tokens // 3))


def _detect_intent(query: str) -> list[str]:
    lowered = query.lower()
    intents = [
        intent
        for intent, keywords in INTENT_KEYWORDS.items()
        if any(keyword in lowered for keyword in keywords)
    ]
    return intents or ["general"]


def _agenda_count(agenda: ResearchAgenda | None) -> int:
    if agenda is None:
        return 0
    return (
        len(agenda.experiments)
        + len(agenda.insights)
        + len(agenda.concepts)
        + len(agenda.snapshots)
    )


def _capabilities_count(capabilities: AgentCapabilities | None) -> int:
    if capabilities is None:
        return 0
    return len(capabilities.roles) + len(capabilities.skills) + len(capabilities.playbooks)


def _research_object_ids(agenda: ResearchAgenda | None) -> list[str]:
    if agenda is None:
        return []
    return [
        *[item.id for item in agenda.experiments],
        *[item.id for item in agenda.insights],
        *[item.id for item in agenda.concepts],
        *[item.id for item in agenda.snapshots],
    ]


def _capability_object_ids(capabilities: AgentCapabilities | None) -> list[str]:
    if capabilities is None:
        return []
    return [
        *[item.id for item in capabilities.roles],
        *[item.id for item in capabilities.skills],
        *[item.id for item in capabilities.playbooks],
    ]


def _section_diag(
    *,
    name: str,
    render_level: str,
    included: int,
    omitted: int,
) -> dict[str, Any]:
    return {
        "name": name,
        "render_level": render_level,
        "objects_included": included,
        "objects_omitted": omitted,
    }


def _render_rank(level: str) -> int:
    return RENDER_LEVEL_RANK.get(level, 0)


def _gather_chunk_candidates(
    conn: sqlite3.Connection,
    query: RetrievalQuery,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> list[list[RetrievalCandidate]]:
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
        vec = collect_vector(
            conn,
            vector_store,
            embedding_provider,
            workspace_id=query.workspace_id,
            query=query.query,
            limit=MAX_VECTOR_HITS,
        )
        if vec:
            rankings.append(vec)
    return rankings
