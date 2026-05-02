"""Final XML envelope assembly + chunk budget fitting.

The structured-section fitter has already chosen what to render in each
priority section; this module renders that selection alongside the
chunk pipeline result, fits the chunks into the remaining token
budget, and returns the final ``<memory_context>`` text plus the
chunks that survived.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.models.behavior import BehaviorInstruction, BehaviorInstructionSet
from agent_memory_lite.models.capabilities import AgentPlaybook, AgentRole, AgentSkill
from agent_memory_lite.models.capability_links import CapabilityLink
from agent_memory_lite.models.core_memory import CoreMemory
from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.procedural import ProceduralRule
from agent_memory_lite.models.retrieval import RetrievalCandidate, ScoredHit
from agent_memory_lite.models.task_state import TaskState
from agent_memory_lite.models.theories import Theory
from agent_memory_lite.retrieval.context_builder_chunks import _ensure_exact_hit_stub
from agent_memory_lite.retrieval.context_builder_constants import (
    STRUCTURED_SAFETY_RESERVE_TOKENS,
)
from agent_memory_lite.retrieval.context_builder_models import StructuredFit
from agent_memory_lite.retrieval.context_builder_render_main import (
    _render,
    _render_structured_only,
)
from agent_memory_lite.retrieval.token_budget import fit_within_budget
from agent_memory_lite.utils.tokens import estimate_tokens


def _render_full_context(
    *,
    core: list[CoreMemory],
    task: TaskState | None,
    rules: list[ProceduralRule],
    facts: list[RetrievalCandidate],
    clipped_hits: list[ScoredHit],
    fit: StructuredFit,
    behavior_instructions: BehaviorInstructionSet | None,
    research_experiment_links: dict[str, list[CapabilityLink]],
    research_insight_links: dict[str, list[CapabilityLink]],
    index_decisions: list[Decision],
    index_theories: list[Theory],
    index_behavior_instructions: list[BehaviorInstruction],
    index_roles: list[AgentRole],
    index_skills: list[AgentSkill],
    index_playbooks: list[AgentPlaybook],
    index_experiments: list[Any],
    index_insights: list[Any],
    index_concepts: list[Any],
    index_snapshots: list[Any],
    max_tokens: int,
) -> tuple[str, list[ScoredHit]]:
    structured_text = _render_structured_only(
        core=core,
        task=task,
        decisions=fit.decisions,
        theories=fit.theories,
        research_agenda=fit.research_agenda,
        research_experiment_links=research_experiment_links,
        research_insight_links=research_insight_links,
        behavior_instructions=behavior_instructions,
        agent_capabilities=fit.agent_capabilities,
        research_render_level=fit.research_render_level,
        capabilities_render_level=fit.capabilities_render_level,
        context_omissions=fit.omissions,
        rules=rules,
        facts=facts,
    )
    chunk_budget = max(
        0, max_tokens - estimate_tokens(structured_text) - STRUCTURED_SAFETY_RESERVE_TOKENS
    )
    chunks_fit = fit_within_budget(clipped_hits, max_tokens=chunk_budget)
    chunks_fit = _ensure_exact_hit_stub(
        clipped_hits,
        chosen=chunks_fit,
        max_tokens=max(0, max_tokens - estimate_tokens(structured_text)),
    )
    text = _render(
        core=core,
        task=task,
        decisions=fit.decisions,
        theories=fit.theories,
        research_agenda=fit.research_agenda,
        research_experiment_links=research_experiment_links,
        research_insight_links=research_insight_links,
        behavior_instructions=behavior_instructions,
        agent_capabilities=fit.agent_capabilities,
        research_render_level=fit.research_render_level,
        capabilities_render_level=fit.capabilities_render_level,
        context_omissions=fit.omissions,
        rules=rules,
        facts=facts,
        hits=chunks_fit,
        index_decisions=index_decisions,
        index_theories=index_theories,
        index_behavior_instructions=index_behavior_instructions,
        index_roles=index_roles,
        index_skills=index_skills,
        index_playbooks=index_playbooks,
        index_experiments=index_experiments,
        index_insights=index_insights,
        index_concepts=index_concepts,
        index_snapshots=index_snapshots,
    )
    return text, chunks_fit
