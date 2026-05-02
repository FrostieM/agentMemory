"""Per-variant evaluation + initial-best baseline for the structured fitter."""

from __future__ import annotations

from agent_memory_lite.models.behavior import BehaviorInstructionSet
from agent_memory_lite.models.capabilities import AgentCapabilities
from agent_memory_lite.models.capability_links import CapabilityLink
from agent_memory_lite.models.core_memory import CoreMemory
from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.procedural import ProceduralRule
from agent_memory_lite.models.research import ResearchAgenda
from agent_memory_lite.models.retrieval import RetrievalCandidate
from agent_memory_lite.models.task_state import TaskState
from agent_memory_lite.retrieval.context_builder_fitting_omissions import (
    _structured_omissions,
    _structured_sections,
)
from agent_memory_lite.retrieval.context_builder_intent import (
    _agenda_count,
    _capabilities_count,
)
from agent_memory_lite.retrieval.context_builder_models import StructuredFit, TheoryContext
from agent_memory_lite.retrieval.context_builder_render_main import _render_structured_only
from agent_memory_lite.utils.tokens import estimate_tokens


def _initial_best(
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
    must_include: list[str],
) -> tuple[StructuredFit, int]:
    best = StructuredFit(
        decisions=decisions,
        theories=theories,
        research_agenda=research_agenda,
        agent_capabilities=agent_capabilities,
        research_render_level="full" if _agenda_count(research_agenda) else "none",
        capabilities_render_level="full" if _capabilities_count(agent_capabilities) else "none",
        sections=[],
        omissions=[],
        must_include=must_include,
    )
    best_tokens = estimate_tokens(
        _render_structured_only(
            core=core,
            task=task,
            decisions=decisions,
            theories=theories,
            research_agenda=research_agenda,
            research_experiment_links=research_experiment_links,
            research_insight_links=research_insight_links,
            behavior_instructions=behavior_instructions,
            agent_capabilities=agent_capabilities,
            rules=rules,
            facts=facts,
            research_render_level=best.research_render_level,
            capabilities_render_level=best.capabilities_render_level,
            context_omissions=[],
        )
    )
    return best, best_tokens


def _evaluate_variant(
    *,
    core: list[CoreMemory],
    task: TaskState | None,
    decision_items: list[Decision],
    theory_items: list[TheoryContext],
    agenda: ResearchAgenda | None,
    agenda_level: str,
    cap: AgentCapabilities | None,
    cap_level: str,
    decisions: list[Decision],
    theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    research_experiment_links: dict[str, list[CapabilityLink]],
    research_insight_links: dict[str, list[CapabilityLink]],
    behavior_instructions: BehaviorInstructionSet | None,
    agent_capabilities: AgentCapabilities | None,
    rules: list[ProceduralRule],
    facts: list[RetrievalCandidate],
    must_include: list[str],
) -> tuple[StructuredFit, int]:
    omissions = _structured_omissions(
        decisions=decisions,
        rendered_decisions=decision_items,
        theories=theories,
        rendered_theories=theory_items,
        research_agenda=research_agenda,
        rendered_research_agenda=agenda,
        agent_capabilities=agent_capabilities,
        rendered_capabilities=cap,
    )
    text = _render_structured_only(
        core=core,
        task=task,
        decisions=decision_items,
        theories=theory_items,
        research_agenda=agenda,
        research_experiment_links=research_experiment_links,
        research_insight_links=research_insight_links,
        behavior_instructions=behavior_instructions,
        agent_capabilities=cap,
        rules=rules,
        facts=facts,
        research_render_level=agenda_level,
        capabilities_render_level=cap_level,
        context_omissions=omissions,
    )
    tokens = estimate_tokens(text)
    fit = StructuredFit(
        decisions=decision_items,
        theories=theory_items,
        research_agenda=agenda,
        agent_capabilities=cap,
        research_render_level=agenda_level,
        capabilities_render_level=cap_level,
        sections=_structured_sections(
            decisions=decisions,
            rendered_decisions=decision_items,
            theories=theories,
            rendered_theories=theory_items,
            research_agenda=research_agenda,
            rendered_research_agenda=agenda,
            research_render_level=agenda_level,
            agent_capabilities=agent_capabilities,
            rendered_capabilities=cap,
            capabilities_render_level=cap_level,
        ),
        omissions=omissions,
        must_include=must_include,
    )
    return fit, tokens
