"""Variant-set builder for the structured-section fitter.

The fitter searches over a small grid of (decisions, theories, agenda,
capabilities) renderings to find the one that maximises score within
``target_tokens``. Building the grid is mechanical; isolating it keeps
the fitter itself readable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_memory_lite.models.capabilities import AgentCapabilities
from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.research import ResearchAgenda
from agent_memory_lite.retrieval.context_builder_intent import (
    _agenda_count,
    _capabilities_count,
    _capability_object_ids,
    _research_object_ids,
)
from agent_memory_lite.retrieval.context_builder_models import TheoryContext


@dataclass(frozen=True, slots=True)
class FitterVariants:
    decision_variants: list[list[Decision]]
    theory_variants: list[list[TheoryContext]]
    agenda_variants: list[tuple[ResearchAgenda | None, str]]
    capability_variants: list[tuple[AgentCapabilities | None, str]]
    protect_research: bool
    protect_capabilities: bool
    protect_decisions: bool
    protect_theories: bool
    must_include: list[str] = field(default_factory=list)


def _build_variants(
    *,
    intent: list[str],
    decisions: list[Decision],
    theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    agent_capabilities: AgentCapabilities | None,
) -> FitterVariants:
    """Pre-compute the section variants the fitter searches over."""
    protect_research = "research" in intent and _agenda_count(research_agenda) > 0
    protect_capabilities = "capability" in intent and _capabilities_count(agent_capabilities) > 0
    protect_decisions = "architecture" in intent and bool(decisions)
    protect_theories = "research" in intent and bool(theories)
    # Pinned decisions are ALWAYS included regardless of intent —
    # operator-anchored architectural invariants must survive every
    # variant the fitter searches over. We split decisions into
    # pinned-first + the rest, then build variants that always keep
    # the pinned head.
    pinned_decisions = [d for d in decisions if getattr(d, "pinned", False)]
    rest_decisions = [d for d in decisions if not getattr(d, "pinned", False)]
    if pinned_decisions:
        decision_variants: list[list[Decision]] = [
            pinned_decisions + rest_decisions,
            pinned_decisions + rest_decisions[:2],
            pinned_decisions + rest_decisions[:1],
            list(pinned_decisions),
        ]
    else:
        decision_variants = [decisions, decisions[:2], decisions[:1]]
        if not protect_decisions:
            decision_variants.append([])
    theory_variants: list[list[TheoryContext]] = [theories, theories[:1]]
    if not protect_theories:
        theory_variants.append([])
    if _agenda_count(research_agenda) > 0:
        agenda_variants: list[tuple[ResearchAgenda | None, str]] = [
            (research_agenda, "full"),
            (research_agenda, "summary"),
            (research_agenda, "stub"),
        ]
    else:
        agenda_variants = [(None, "none")]
    if not protect_research and (None, "none") not in agenda_variants:
        agenda_variants.append((None, "none"))
    if _capabilities_count(agent_capabilities) > 0:
        capability_variants: list[tuple[AgentCapabilities | None, str]] = [
            (agent_capabilities, "full"),
            (agent_capabilities, "summary"),
            (agent_capabilities, "stub"),
        ]
    else:
        capability_variants = [(None, "none")]
    if not protect_capabilities and (None, "none") not in capability_variants:
        capability_variants.append((None, "none"))
    must_include = [
        *(d.id for d in pinned_decisions),
        *([item.id for item in decisions] if protect_decisions else []),
        *([bundle.theory.id for bundle in theories] if protect_theories else []),
        *(_research_object_ids(research_agenda) if protect_research else []),
        *(_capability_object_ids(agent_capabilities) if protect_capabilities else []),
    ]
    return FitterVariants(
        decision_variants=decision_variants,
        theory_variants=theory_variants,
        agenda_variants=agenda_variants,
        capability_variants=capability_variants,
        protect_research=protect_research,
        protect_capabilities=protect_capabilities,
        protect_decisions=protect_decisions,
        protect_theories=protect_theories,
        must_include=must_include,
    )
