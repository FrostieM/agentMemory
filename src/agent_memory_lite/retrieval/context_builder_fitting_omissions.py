"""Omission + section-diagnostic builders for the structured fitter.

When the budget fitter drops a section (e.g. ``research_agenda``), the
items go into a ``<context_omissions>`` block so the agent can still
discover that they exist. ``_section_diag`` summarises what made it
through.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.models.capabilities import AgentCapabilities
from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.research import ResearchAgenda
from agent_memory_lite.retrieval.context_builder_intent import (
    _agenda_count,
    _capabilities_count,
    _section_diag,
)
from agent_memory_lite.retrieval.context_builder_models import TheoryContext


def _omission(section: str, kind: str, id_value: str, title: str) -> dict[str, Any]:
    return {
        "section": section,
        "type": kind,
        "id": id_value,
        "title": title,
        "relevance": "elastic",
    }


def _structured_omissions(
    *,
    decisions: list[Decision],
    rendered_decisions: list[Decision],
    theories: list[TheoryContext],
    rendered_theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    rendered_research_agenda: ResearchAgenda | None,
    agent_capabilities: AgentCapabilities | None,
    rendered_capabilities: AgentCapabilities | None,
) -> list[dict[str, Any]]:
    omissions: list[dict[str, Any]] = []
    if research_agenda is not None and rendered_research_agenda is None:
        for experiment in research_agenda.experiments:
            omissions.append(
                _omission("research_agenda", "experiment", experiment.id, experiment.title)
            )
        for insight in research_agenda.insights:
            omissions.append(_omission("research_agenda", "insight", insight.id, insight.summary))
        for concept in research_agenda.concepts:
            omissions.append(_omission("research_agenda", "concept", concept.id, concept.name))
        for snapshot in research_agenda.snapshots:
            omissions.append(_omission("research_agenda", "snapshot", snapshot.id, snapshot.title))
    if agent_capabilities is not None and rendered_capabilities is None:
        for role in agent_capabilities.roles:
            omissions.append(_omission("agent_capabilities", "role", role.id, role.name))
        for skill in agent_capabilities.skills:
            omissions.append(_omission("agent_capabilities", "skill", skill.id, skill.name))
        for playbook in agent_capabilities.playbooks:
            omissions.append(
                _omission("agent_capabilities", "playbook", playbook.id, playbook.name)
            )
    return omissions


def _structured_sections(
    *,
    decisions: list[Decision],
    rendered_decisions: list[Decision],
    theories: list[TheoryContext],
    rendered_theories: list[TheoryContext],
    research_agenda: ResearchAgenda | None,
    rendered_research_agenda: ResearchAgenda | None,
    research_render_level: str,
    agent_capabilities: AgentCapabilities | None,
    rendered_capabilities: AgentCapabilities | None,
    capabilities_render_level: str,
) -> list[dict[str, Any]]:
    return [
        _section_diag(
            name="active_decisions",
            render_level="full" if rendered_decisions else "none",
            included=len(rendered_decisions),
            omitted=max(0, len(decisions) - len(rendered_decisions)),
        ),
        _section_diag(
            name="active_theories",
            render_level="full" if rendered_theories else "none",
            included=len(rendered_theories),
            omitted=max(0, len(theories) - len(rendered_theories)),
        ),
        _section_diag(
            name="research_agenda",
            render_level=research_render_level if rendered_research_agenda is not None else "none",
            included=_agenda_count(rendered_research_agenda),
            omitted=max(
                0, _agenda_count(research_agenda) - _agenda_count(rendered_research_agenda)
            ),
        ),
        _section_diag(
            name="agent_capabilities",
            render_level=(
                capabilities_render_level if rendered_capabilities is not None else "none"
            ),
            included=_capabilities_count(rendered_capabilities),
            omitted=max(
                0,
                _capabilities_count(agent_capabilities)
                - _capabilities_count(rendered_capabilities),
            ),
        ),
    ]
