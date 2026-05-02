"""Top-level XML envelope assembler.

Glue that calls every section renderer in priority order to produce the
full ``<memory_context>`` block (or the structured-only variant used by
the budget fitter).
"""

from __future__ import annotations

from typing import Any

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
from agent_memory_lite.models.research import ResearchAgenda
from agent_memory_lite.models.retrieval import RetrievalCandidate, ScoredHit
from agent_memory_lite.models.task_state import TaskState
from agent_memory_lite.models.theories import Theory
from agent_memory_lite.retrieval.context_builder_models import TheoryContext
from agent_memory_lite.retrieval.context_builder_render_behavior import (
    _render_behavior_instructions,
)
from agent_memory_lite.retrieval.context_builder_render_capabilities import (
    _render_agent_capabilities,
)
from agent_memory_lite.retrieval.context_builder_render_core import (
    _render_core,
    _render_decisions,
    _render_task,
)
from agent_memory_lite.retrieval.context_builder_render_misc import (
    _render_chunks,
    _render_context_omissions,
    _render_facts,
    _render_rules,
)
from agent_memory_lite.retrieval.context_builder_render_research import (
    _render_research_agenda_with_links,
)
from agent_memory_lite.retrieval.context_builder_render_theory import _render_theories


def _render(
    *,
    core: list[CoreMemory],
    task: TaskState | None,
    decisions: list[Decision],
    theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    research_experiment_links: dict[str, list[CapabilityLink]],
    research_insight_links: dict[str, list[CapabilityLink]],
    behavior_instructions: BehaviorInstructionSet | None,
    agent_capabilities: AgentCapabilities | None,
    rules: list[ProceduralRule],
    facts: list[RetrievalCandidate],
    hits: list[ScoredHit],
    research_render_level: str = "full",
    capabilities_render_level: str = "full",
    context_omissions: list[dict[str, Any]] | None = None,
    index_decisions: list[Decision] | None = None,
    index_theories: list[Theory] | None = None,
    index_behavior_instructions: list[BehaviorInstruction] | None = None,
    index_roles: list[AgentRole] | None = None,
    index_skills: list[AgentSkill] | None = None,
    index_playbooks: list[AgentPlaybook] | None = None,
    index_experiments: list[Any] | None = None,
    index_insights: list[Any] | None = None,
    index_concepts: list[Any] | None = None,
    index_snapshots: list[Any] | None = None,
) -> str:
    lines = ["<memory_context>"]
    lines.extend(_render_core(core))
    lines.extend(
        _render_behavior_instructions(
            behavior_instructions, index_items=index_behavior_instructions
        )
    )
    lines.extend(_render_task(task))
    lines.extend(_render_decisions(decisions, index_items=index_decisions))
    lines.extend(_render_theories(theories, index_items=index_theories))
    lines.extend(
        _render_research_agenda_with_links(
            research_agenda,
            experiment_links=research_experiment_links,
            insight_links=research_insight_links,
            render_level=research_render_level,
            why_relevant="query intent reserved research agenda",
            index_experiments=index_experiments,
            index_insights=index_insights,
            index_concepts=index_concepts,
            index_snapshots=index_snapshots,
        )
    )
    lines.extend(
        _render_agent_capabilities(
            agent_capabilities,
            render_level=capabilities_render_level,
            why_relevant="query intent reserved agent capabilities",
            index_roles=index_roles,
            index_skills=index_skills,
            index_playbooks=index_playbooks,
        )
    )
    lines.extend(_render_rules(rules))
    lines.extend(_render_context_omissions(context_omissions or []))
    lines.extend(_render_facts(facts))
    lines.extend(_render_chunks(hits))
    lines.append("</memory_context>")
    return "\n".join(lines)


def _render_structured_only(**kwargs: Any) -> str:
    """Variant of ``_render`` that forces ``hits=[]``.

    Used by the budget fitter to estimate the structured-section size
    without paying for the chunk render.
    """
    kwargs["hits"] = []
    return _render(**kwargs)
