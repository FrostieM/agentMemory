"""Structured-section budget fitter for the context builder.

Walks the variant grid built by ``_build_variants`` and picks the best
fit. Variant evaluation, scoring, and the stub-fallback / sections
backfill logic live in ``context_builder_fitting_eval.py`` and
``context_builder_fitting_finalize.py``.
"""

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
from agent_memory_lite.retrieval.context_builder_fitting_eval import (
    _evaluate_variant,
    _initial_best,
)
from agent_memory_lite.retrieval.context_builder_fitting_finalize import _finalize_fit
from agent_memory_lite.retrieval.context_builder_fitting_variants import _build_variants
from agent_memory_lite.retrieval.context_builder_intent import (
    _agenda_count,
    _capabilities_count,
    _render_rank,
)
from agent_memory_lite.retrieval.context_builder_models import StructuredFit, TheoryContext


def _score(
    *,
    decision_items: list[Decision],
    theory_items: list[TheoryContext],
    agenda: ResearchAgenda | None,
    agenda_level: str,
    cap: AgentCapabilities | None,
    cap_level: str,
    omissions_count: int,
) -> int:
    return (
        len(decision_items) * 30
        + len(theory_items) * 40
        + _render_rank(agenda_level) * max(_agenda_count(agenda), 1) * 12
        + _render_rank(cap_level) * max(_capabilities_count(cap), 1) * 8
        - omissions_count * 10
    )


def _fit_structured_sections(
    *,
    max_tokens: int,
    chunk_reserve_tokens: int,
    intent: list[str],
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
) -> StructuredFit:
    variants = _build_variants(
        intent=intent,
        decisions=decisions,
        theories=theories,
        research_agenda=research_agenda,
        agent_capabilities=agent_capabilities,
    )
    must_include = list(variants.must_include)
    if task is not None:
        must_include = [task.task_id, *must_include]

    target_tokens = max(0, max_tokens - chunk_reserve_tokens)
    best, best_tokens = _initial_best(
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
        must_include=must_include,
    )
    best_score = -1

    for decision_items in variants.decision_variants:
        for theory_items in variants.theory_variants:
            for agenda, agenda_level in variants.agenda_variants:
                for cap, cap_level in variants.capability_variants:
                    fit, tokens = _evaluate_variant(
                        core=core,
                        task=task,
                        decision_items=decision_items,
                        theory_items=theory_items,
                        agenda=agenda,
                        agenda_level=agenda_level,
                        cap=cap,
                        cap_level=cap_level,
                        decisions=decisions,
                        theories=theories,
                        research_agenda=research_agenda,
                        research_experiment_links=research_experiment_links,
                        research_insight_links=research_insight_links,
                        behavior_instructions=behavior_instructions,
                        agent_capabilities=agent_capabilities,
                        rules=rules,
                        facts=facts,
                        must_include=must_include,
                    )
                    score = _score(
                        decision_items=decision_items,
                        theory_items=theory_items,
                        agenda=agenda,
                        agenda_level=agenda_level,
                        cap=cap,
                        cap_level=cap_level,
                        omissions_count=len(fit.omissions),
                    )
                    if tokens <= target_tokens and score > best_score:
                        best, best_tokens, best_score = fit, tokens, score
                    elif best_score < 0 and tokens < best_tokens:
                        best, best_tokens = fit, tokens
    return _finalize_fit(
        best=best,
        best_score=best_score,
        decisions=decisions,
        theories=theories,
        research_agenda=research_agenda,
        agent_capabilities=agent_capabilities,
        variants=variants,
        must_include=must_include,
    )
