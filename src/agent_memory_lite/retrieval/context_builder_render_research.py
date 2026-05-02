"""Renderer for the ``<research_agenda>`` section.

Per-item renderers live in
``context_builder_render_research_items.py``; this module just walks the
agenda, calls them, and adds per-kind ``<index>`` blocks for the long
tail.
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape

from agent_memory_lite.models.capability_links import CapabilityLink
from agent_memory_lite.models.research import ResearchAgenda
from agent_memory_lite.retrieval.context_builder_constants import MAX_TEXT_CHARS
from agent_memory_lite.retrieval.context_builder_render_research_items import (
    _render_concept,
    _render_experiment,
    _render_insight,
    _render_snapshot,
)
from agent_memory_lite.retrieval.context_builder_text import _clip_text, _render_index_block


def _agenda_index_blocks(
    agenda: ResearchAgenda,
    *,
    index_experiments: list[Any],
    index_insights: list[Any],
    index_concepts: list[Any],
    index_snapshots: list[Any],
) -> list[str]:
    """Per-kind ``<index>`` blocks for the agenda long tail."""
    lines: list[str] = []
    if index_experiments:
        long_tail = [
            (exp.id, exp.title, {"kind": "experiment", "status": exp.status.value})
            for exp in index_experiments
        ]
        lines.extend(
            _render_index_block(
                full_count=len(agenda.experiments), long_tail=long_tail, indent="    "
            )
        )
    if index_insights:
        long_tail = [
            (ins.id, ins.summary, {"kind": "insight", "status": ins.status.value})
            for ins in index_insights
        ]
        lines.extend(
            _render_index_block(full_count=len(agenda.insights), long_tail=long_tail, indent="    ")
        )
    if index_concepts:
        long_tail = [
            (con.id, con.name, {"kind": "concept", "concept_kind": con.kind.value})
            for con in index_concepts
        ]
        lines.extend(
            _render_index_block(full_count=len(agenda.concepts), long_tail=long_tail, indent="    ")
        )
    if index_snapshots:
        long_tail = [
            (snap.id, snap.title, {"kind": "snapshot", "key": snap.snapshot_key})
            for snap in index_snapshots
        ]
        lines.extend(
            _render_index_block(
                full_count=len(agenda.snapshots), long_tail=long_tail, indent="    "
            )
        )
    return lines


def _render_research_agenda_with_links(
    agenda: ResearchAgenda | None,
    *,
    experiment_links: dict[str, list[CapabilityLink]],
    insight_links: dict[str, list[CapabilityLink]],
    render_level: str = "full",
    why_relevant: str = "",
    index_experiments: list[Any] | None = None,
    index_insights: list[Any] | None = None,
    index_concepts: list[Any] | None = None,
    index_snapshots: list[Any] | None = None,
) -> list[str]:
    index_experiments = index_experiments or []
    index_insights = index_insights or []
    index_concepts = index_concepts or []
    index_snapshots = index_snapshots or []
    has_full = agenda is not None and (
        agenda.snapshots or agenda.experiments or agenda.insights or agenda.concepts
    )
    has_index = bool(index_experiments or index_insights or index_concepts or index_snapshots)
    if not has_full and not has_index:
        return ["  <research_agenda/>"]
    if agenda is None:
        agenda = ResearchAgenda(snapshots=[], experiments=[], insights=[], concepts=[])

    lines = ["  <research_agenda>"]
    if why_relevant:
        lines.append(
            f"    <why_relevant>{escape(_clip_text(why_relevant, MAX_TEXT_CHARS))}</why_relevant>"
        )
    for experiment in agenda.experiments:
        lines.extend(
            _render_experiment(
                experiment, render_level=render_level, experiment_links=experiment_links
            )
        )
    for insight in agenda.insights:
        lines.extend(
            _render_insight(insight, render_level=render_level, insight_links=insight_links)
        )
    for concept in agenda.concepts:
        lines.extend(_render_concept(concept, render_level=render_level))
    for snapshot in agenda.snapshots:
        lines.extend(_render_snapshot(snapshot, render_level=render_level))
    lines.extend(
        _agenda_index_blocks(
            agenda,
            index_experiments=index_experiments,
            index_insights=index_insights,
            index_concepts=index_concepts,
            index_snapshots=index_snapshots,
        )
    )
    lines.append("  </research_agenda>")
    return lines


def _render_research_agenda(agenda: ResearchAgenda | None) -> list[str]:
    return _render_research_agenda_with_links(
        agenda,
        experiment_links={},
        insight_links={},
        render_level="full",
    )
