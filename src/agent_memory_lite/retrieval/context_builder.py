"""Final context block builder.

Produces the XML-like envelope the agent consumes:

    <memory_context>
      <retrieved_chunks>
        <chunk id="..." path="..." score="0.83" sources="fts,vector">
          ...text...
        </chunk>
      </retrieved_chunks>
    </memory_context>

Phase 2 wires only `<retrieved_chunks>`. Core memory, task state, decisions,
procedural rules, and graph facts join in Phase 3 and Phase 4.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.models.retrieval import (
    RetrievalCandidate,
    RetrievalQuery,
    ScoredHit,
)
from agent_memory_lite.retrieval.candidates_fts import collect_fts
from agent_memory_lite.retrieval.candidates_vector import collect_vector
from agent_memory_lite.retrieval.filters import filter_active
from agent_memory_lite.retrieval.fusion_rrf import reciprocal_rank_fusion
from agent_memory_lite.retrieval.normalize import NormalizedQuery, normalize
from agent_memory_lite.retrieval.scoring import score_candidates
from agent_memory_lite.retrieval.token_budget import fit_within_budget
from agent_memory_lite.vector_store.base import VectorStore

MAX_FTS_HITS = 30
MAX_VECTOR_HITS = 30


@dataclass(frozen=True, slots=True)
class BuiltContext:
    text: str
    hits: list[ScoredHit]
    normalized: NormalizedQuery


def _gather_candidates(
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


def _render(hits: list[ScoredHit]) -> str:
    if not hits:
        return "<memory_context>\n  <retrieved_chunks/>\n</memory_context>"
    lines = ["<memory_context>", "  <retrieved_chunks>"]
    for hit in hits:
        attrs = (
            f"id={quoteattr(hit.id)} "
            f"path={quoteattr(hit.path or '')} "
            f"score={quoteattr(f'{hit.score:.4f}')} "
            f"sources={quoteattr(','.join(hit.sources))}"
        )
        lines.append(f"    <chunk {attrs}>")
        lines.append(f"      {escape(hit.text)}")
        lines.append("    </chunk>")
    lines.append("  </retrieved_chunks>")
    lines.append("</memory_context>")
    return "\n".join(lines)


def build_context(
    conn: sqlite3.Connection,
    query: RetrievalQuery,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
) -> BuiltContext:
    normalized = normalize(query.query)
    rankings = _gather_candidates(
        conn, query, embedding_provider=embedding_provider, vector_store=vector_store
    )
    fused = reciprocal_rank_fusion(rankings)
    scored = score_candidates(fused)
    filtered = filter_active(scored, historical=query.historical)
    fit = fit_within_budget(filtered, max_tokens=query.max_tokens)
    return BuiltContext(text=_render(fit), hits=fit, normalized=normalized)
