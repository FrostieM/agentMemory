"""Final context block builder.

Produces the XML-like envelope the agent consumes::

    <memory_context>
      <core_memory>...</core_memory>
      <behavior_instructions>...</behavior_instructions>
      <task_state>...</task_state>
      <active_decisions>...</active_decisions>
      <active_theories>...</active_theories>
      <research_agenda>...</research_agenda>
      <agent_capabilities>...</agent_capabilities>
      <procedural_rules>...</procedural_rules>
      <retrieved_facts>...</retrieved_facts>
      <retrieved_chunks>...</retrieved_chunks>
    </memory_context>

The orchestrator fans out into ``context_builder_*`` modules:

* ``_chunks``   - chunk pipeline (mojibake / score / feedback / stub).
* ``_constants`` - per-section caps and intent keywords.
* ``_finalize`` - final XML assembly + chunk budget fit.
* ``_fitting``  - structured-section budget fitter.
* ``_gather``   - per-section repository gather + slice.
* ``_intent``   - query intent detection + chunk reserve.
* ``_pipeline`` - chunk pipeline + bundled gather result.
* ``_render_*`` - per-section renderers.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.models.retrieval import RetrievalQuery
from agent_memory_lite.retrieval.context_builder_chunks import filter_context_hits
from agent_memory_lite.retrieval.context_builder_finalize import _render_full_context
from agent_memory_lite.retrieval.context_builder_fitting import _fit_structured_sections
from agent_memory_lite.retrieval.context_builder_intent import (
    _chunk_reserve_tokens,
    _detect_intent,
)
from agent_memory_lite.retrieval.context_builder_models import (
    BuiltContext,
    TheoryContext,
)
from agent_memory_lite.retrieval.context_builder_pipeline import (
    _build_chunk_pipeline,
    gather_sections,
)
from agent_memory_lite.retrieval.normalize import normalize
from agent_memory_lite.utils.tokens import estimate_tokens
from agent_memory_lite.vector_store.base import VectorStore


def build_context(
    conn: sqlite3.Connection,
    query: RetrievalQuery,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
) -> BuiltContext:
    normalized = normalize(query.query)
    clipped_hits = _build_chunk_pipeline(
        conn, query, embedding_provider=embedding_provider, vector_store=vector_store
    )
    sections = gather_sections(conn, query)
    intent = _detect_intent(query.query)
    fit = _fit_structured_sections(
        max_tokens=query.max_tokens,
        chunk_reserve_tokens=_chunk_reserve_tokens(query.max_tokens, has_hits=bool(clipped_hits)),
        intent=intent,
        core=sections.core,
        task=sections.task,
        decisions=sections.decisions,
        theories=sections.theories,
        research_agenda=sections.research_agenda,
        research_experiment_links=sections.research_experiment_links,
        research_insight_links=sections.research_insight_links,
        behavior_instructions=sections.behavior_instructions,
        agent_capabilities=sections.agent_capabilities,
        rules=sections.rules,
        facts=sections.facts,
    )
    text, chunks_fit = _render_full_context(
        core=sections.core,
        task=sections.task,
        rules=sections.rules,
        facts=sections.facts,
        clipped_hits=clipped_hits,
        fit=fit,
        behavior_instructions=sections.behavior_instructions,
        research_experiment_links=sections.research_experiment_links,
        research_insight_links=sections.research_insight_links,
        index_decisions=sections.index_decisions,
        index_theories=sections.index_theories,
        index_behavior_instructions=sections.index_behavior_instructions,
        index_roles=sections.index_roles,
        index_skills=sections.index_skills,
        index_playbooks=sections.index_playbooks,
        index_experiments=sections.index_experiments,
        index_insights=sections.index_insights,
        index_concepts=sections.index_concepts,
        index_snapshots=sections.index_snapshots,
        max_tokens=query.max_tokens,
    )
    return BuiltContext(
        text=text,
        hits=chunks_fit,
        facts=sections.facts,
        normalized=normalized,
        core=sections.core,
        task_state=sections.task,
        decisions=fit.decisions,
        theories=fit.theories,
        research_agenda=fit.research_agenda,
        behavior_instructions=sections.behavior_instructions,
        agent_capabilities=fit.agent_capabilities,
        rules=sections.rules,
        budget_diagnostics={
            "max_tokens": query.max_tokens,
            "estimated_tokens": estimate_tokens(text),
            "intent": intent,
            "sections": fit.sections,
            "must_include": fit.must_include,
            "omissions": fit.omissions,
        },
    )


__all__ = [
    "BuiltContext",
    "TheoryContext",
    "build_context",
    "filter_context_hits",
]
