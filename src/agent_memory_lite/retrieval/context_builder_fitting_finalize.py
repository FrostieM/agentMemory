"""Final-fit polish for the structured fitter.

Stub-fallback (no variant fit within budget) and sections/omissions
backfill (when the chosen variant left ``best.sections`` empty). Pulled
out of ``context_builder_fitting_eval.py`` so that module can stay
under the SLOC ceiling.
"""

from __future__ import annotations

from agent_memory_lite.models.capabilities import AgentCapabilities
from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.research import ResearchAgenda
from agent_memory_lite.retrieval.context_builder_fitting_omissions import (
    _structured_omissions,
    _structured_sections,
)
from agent_memory_lite.retrieval.context_builder_fitting_variants import FitterVariants
from agent_memory_lite.retrieval.context_builder_models import StructuredFit, TheoryContext


def _stub_fallback_fit(
    *,
    decisions: list[Decision],
    theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    agent_capabilities: AgentCapabilities | None,
    variants: FitterVariants,
    must_include: list[str],
) -> StructuredFit:
    # Even when the budget grid produced no full-fit variant, every
    # section that has data should surface at least one representative
    # item. Otherwise an unprotected intent (anything that didn't match
    # "architecture" / "research" / "capability") on a populated
    # workspace - copyBot is the canonical example with 91 decisions,
    # 6 theories, 18 capabilities - silently renders only behavior
    # instructions, leaving the agent blind to the rest of the
    # structured memory it could have used. Render level is "stub" for
    # research/capabilities (compact ``id``/``title``/``status``
    # entries) and decisions/theories shrink to a single item, so the
    # extra cost is bounded but the surface is no longer "empty".
    rendered_decisions = decisions[:1] if decisions else []
    rendered_theories = theories[:1] if theories else []
    rendered_research_agenda = research_agenda if research_agenda is not None else None
    rendered_capabilities = agent_capabilities if agent_capabilities is not None else None
    research_level = "stub" if rendered_research_agenda is not None else "none"
    capability_level = "stub" if rendered_capabilities is not None else "none"
    return StructuredFit(
        decisions=rendered_decisions,
        theories=rendered_theories,
        research_agenda=rendered_research_agenda,
        agent_capabilities=rendered_capabilities,
        research_render_level=research_level,
        capabilities_render_level=capability_level,
        sections=_structured_sections(
            decisions=decisions,
            rendered_decisions=rendered_decisions,
            theories=theories,
            rendered_theories=rendered_theories,
            research_agenda=research_agenda,
            rendered_research_agenda=rendered_research_agenda,
            research_render_level=research_level,
            agent_capabilities=agent_capabilities,
            rendered_capabilities=rendered_capabilities,
            capabilities_render_level=capability_level,
        ),
        omissions=[],
        must_include=must_include,
    )


def _backfill_sections(
    best: StructuredFit,
    *,
    decisions: list[Decision],
    theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    agent_capabilities: AgentCapabilities | None,
    must_include: list[str],
) -> StructuredFit:
    return StructuredFit(
        decisions=best.decisions,
        theories=best.theories,
        research_agenda=best.research_agenda,
        agent_capabilities=best.agent_capabilities,
        research_render_level=best.research_render_level,
        capabilities_render_level=best.capabilities_render_level,
        sections=_structured_sections(
            decisions=decisions,
            rendered_decisions=best.decisions,
            theories=theories,
            rendered_theories=best.theories,
            research_agenda=research_agenda,
            rendered_research_agenda=best.research_agenda,
            research_render_level=best.research_render_level,
            agent_capabilities=agent_capabilities,
            rendered_capabilities=best.agent_capabilities,
            capabilities_render_level=best.capabilities_render_level,
        ),
        omissions=_structured_omissions(
            decisions=decisions,
            rendered_decisions=best.decisions,
            theories=theories,
            rendered_theories=best.theories,
            research_agenda=research_agenda,
            rendered_research_agenda=best.research_agenda,
            agent_capabilities=agent_capabilities,
            rendered_capabilities=best.agent_capabilities,
        ),
        must_include=must_include,
    )


def _finalize_fit(
    *,
    best: StructuredFit,
    best_score: int,
    decisions: list[Decision],
    theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    agent_capabilities: AgentCapabilities | None,
    variants: FitterVariants,
    must_include: list[str],
) -> StructuredFit:
    if best_score < 0:
        best = _stub_fallback_fit(
            decisions=decisions,
            theories=theories,
            research_agenda=research_agenda,
            agent_capabilities=agent_capabilities,
            variants=variants,
            must_include=must_include,
        )
    if not best.sections:
        best = _backfill_sections(
            best,
            decisions=decisions,
            theories=theories,
            research_agenda=research_agenda,
            agent_capabilities=agent_capabilities,
            must_include=must_include,
        )
    return best
