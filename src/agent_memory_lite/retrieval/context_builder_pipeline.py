"""Compose the chunk pipeline + bundle the gather results.

``build_context`` does three rounds of work: chunk pipeline, structured
gather, fit + render. The first two land here so the orchestrator stays
focused on the high-level shape.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.embeddings.base import EmbeddingProvider
from agent_memory_lite.models.behavior import BehaviorInstruction, BehaviorInstructionSet
from agent_memory_lite.models.capabilities import (
    AgentCapabilities,
    AgentPlaybook,
    AgentRole,
    AgentSkill,
)
from agent_memory_lite.models.capability_links import CapabilityLink
from agent_memory_lite.models.core_memory import CoreMemory
from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.procedural import ProceduralRule
from agent_memory_lite.models.research import Experiment, MemorySnapshot, ResearchAgenda
from agent_memory_lite.models.research_taxonomy import DomainConcept, ResearchInsight
from agent_memory_lite.models.retrieval import (
    RetrievalCandidate,
    RetrievalQuery,
    ScoredHit,
)
from agent_memory_lite.models.task_state import TaskState
from agent_memory_lite.models.theories import Theory
from agent_memory_lite.repositories.core_memory_repo import list_active_core
from agent_memory_lite.repositories.procedural_repo import list_active_rules
from agent_memory_lite.repositories.task_state_repo import get_task_state
from agent_memory_lite.retrieval.candidates_graph import collect_graph
from agent_memory_lite.retrieval.context_builder_chunks import (
    _apply_usage_feedback,
    _clip_hits_for_context,
    filter_context_hits,
)
from agent_memory_lite.retrieval.context_builder_constants import MAX_GRAPH_HITS
from agent_memory_lite.retrieval.context_builder_gather import (
    _build_theory_contexts,
    _gather_behavior,
    _gather_capabilities,
    _gather_decisions,
    _gather_research_agenda,
    _gather_theories,
)
from agent_memory_lite.retrieval.context_builder_intent import _gather_chunk_candidates
from agent_memory_lite.retrieval.context_builder_models import TheoryContext
from agent_memory_lite.retrieval.filters import filter_active
from agent_memory_lite.retrieval.fusion_rrf import reciprocal_rank_fusion
from agent_memory_lite.retrieval.scoring import score_candidates
from agent_memory_lite.retrieval.scoring_decay import apply_confidence_decay
from agent_memory_lite.vector_store.base import VectorStore


def _build_chunk_pipeline(
    conn: sqlite3.Connection,
    query: RetrievalQuery,
    *,
    embedding_provider: EmbeddingProvider | None,
    vector_store: VectorStore | None,
) -> list[ScoredHit]:
    rankings = _gather_chunk_candidates(
        conn, query, embedding_provider=embedding_provider, vector_store=vector_store
    )
    fused = reciprocal_rank_fusion(rankings)
    scored = score_candidates(fused)
    decayed = apply_confidence_decay(scored)
    feedback_scored = _apply_usage_feedback(conn, workspace_id=query.workspace_id, hits=decayed)
    filtered = filter_active(feedback_scored, historical=query.historical)
    quality_filtered = filter_context_hits(filtered, historical=query.historical)
    return _clip_hits_for_context(quality_filtered)


@dataclass(frozen=True, slots=True)
class GatheredSections:
    core: list[CoreMemory]
    rules: list[ProceduralRule]
    task: TaskState | None
    facts: list[RetrievalCandidate]
    decisions: list[Decision]
    index_decisions: list[Decision]
    theory_items: list[Theory]
    index_theories: list[Theory]
    theories: list[TheoryContext]
    research_agenda: ResearchAgenda
    index_snapshots: list[MemorySnapshot]
    index_experiments: list[Experiment]
    index_insights: list[ResearchInsight]
    index_concepts: list[DomainConcept]
    research_experiment_links: dict[str, list[CapabilityLink]]
    research_insight_links: dict[str, list[CapabilityLink]]
    behavior_instructions: BehaviorInstructionSet | None
    index_behavior_instructions: list[BehaviorInstruction]
    agent_capabilities: AgentCapabilities | None
    index_roles: list[AgentRole]
    index_skills: list[AgentSkill]
    index_playbooks: list[AgentPlaybook]


def gather_sections(conn: sqlite3.Connection, query: RetrievalQuery) -> GatheredSections:
    """Run every per-section gather. The orchestrator just shuffles the result."""
    facts = collect_graph(
        conn,
        workspace_id=query.workspace_id,
        query=query.query,
        limit=MAX_GRAPH_HITS,
        historical=query.historical,
    )
    core = list_active_core(conn, query.workspace_id)
    rules = list_active_rules(conn, query.workspace_id)
    decisions, index_decisions = _gather_decisions(conn, query)
    theory_items, index_theories = _gather_theories(conn, query)
    theories = _build_theory_contexts(conn, workspace_id=query.workspace_id, theories=theory_items)
    (
        research_agenda,
        index_snapshots,
        index_experiments,
        index_insights,
        index_concepts,
        research_experiment_links,
        research_insight_links,
    ) = _gather_research_agenda(conn, query)
    behavior_instructions, index_behavior_instructions = _gather_behavior(conn, query)
    agent_capabilities, index_roles, index_skills, index_playbooks = _gather_capabilities(
        conn, query
    )
    task = get_task_state(conn, query.workspace_id, query.task_id) if query.task_id else None
    return GatheredSections(
        core=core,
        rules=rules,
        task=task,
        facts=facts,
        decisions=decisions,
        index_decisions=index_decisions,
        theory_items=theory_items,
        index_theories=index_theories,
        theories=theories,
        research_agenda=research_agenda,
        index_snapshots=index_snapshots,
        index_experiments=index_experiments,
        index_insights=index_insights,
        index_concepts=index_concepts,
        research_experiment_links=research_experiment_links,
        research_insight_links=research_insight_links,
        behavior_instructions=behavior_instructions,
        index_behavior_instructions=index_behavior_instructions,
        agent_capabilities=agent_capabilities,
        index_roles=index_roles,
        index_skills=index_skills,
        index_playbooks=index_playbooks,
    )
