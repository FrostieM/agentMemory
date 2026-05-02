"""Per-item renderers for ``<research_agenda>`` (experiments, insights,
concepts, snapshots). Pulled out so the orchestrator stays under the
SLOC ceiling.
"""

from __future__ import annotations

import json
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from agent_memory_lite.models.capability_links import CapabilityLink
from agent_memory_lite.retrieval.context_builder_constants import (
    MAX_COMMAND_CHARS,
    MAX_LIST_ITEM_CHARS,
    MAX_TEXT_CHARS,
    MAX_TITLE_CHARS,
)
from agent_memory_lite.retrieval.context_builder_render_theory import _render_capability_links
from agent_memory_lite.retrieval.context_builder_text import _clip_text


def _render_experiment(
    experiment: Any,
    *,
    render_level: str,
    experiment_links: dict[str, list[CapabilityLink]],
) -> list[str]:
    attrs = (
        f"id={quoteattr(experiment.id)} "
        f"status={quoteattr(experiment.status.value)} "
        f"priority={quoteattr(f'{experiment.priority:.2f}')} "
        f"theory_id={quoteattr(experiment.theory_id or '')} "
        f"snapshot_id={quoteattr(experiment.snapshot_id or '')} "
        f"render_level={quoteattr(render_level)}"
    )
    lines = [f"    <experiment {attrs}>"]
    lines.append(f"      <title>{escape(_clip_text(experiment.title, MAX_TITLE_CHARS))}</title>")
    if render_level == "stub":
        if experiment.source_episode_id:
            lines.append(f"      <source>{escape(experiment.source_episode_id)}</source>")
        lines.append("    </experiment>")
        return lines
    hyp_chars = MAX_TEXT_CHARS if render_level == "full" else MAX_LIST_ITEM_CHARS
    lines.append(
        f"      <hypothesis>{escape(_clip_text(experiment.hypothesis, hyp_chars))}</hypothesis>"
    )
    if render_level == "full":
        if experiment.cohort_definition:
            lines.append(
                f"      <cohort>{escape(_clip_text(experiment.cohort_definition, MAX_TEXT_CHARS))}</cohort>"
            )
        if experiment.success_criteria:
            criteria = json.dumps(experiment.success_criteria, sort_keys=True)
            lines.append(
                f"      <success_criteria>{escape(_clip_text(criteria, MAX_COMMAND_CHARS))}</success_criteria>"
            )
        if experiment.command:
            lines.append(
                f"      <command>{escape(_clip_text(experiment.command, MAX_COMMAND_CHARS))}</command>"
            )
        lines.extend(_render_capability_links(experiment_links.get(experiment.id, [])))
    lines.append("    </experiment>")
    return lines


def _render_insight(
    insight: Any,
    *,
    render_level: str,
    insight_links: dict[str, list[CapabilityLink]],
) -> list[str]:
    attrs = (
        f"id={quoteattr(insight.id)} "
        f"type={quoteattr(insight.insight_type.value)} "
        f"status={quoteattr(insight.status.value)} "
        f"confidence={quoteattr(f'{insight.confidence:.2f}')} "
        f"target_type={quoteattr(insight.target_type or '')} "
        f"target_id={quoteattr(insight.target_id or '')} "
        f"render_level={quoteattr(render_level)}"
    )
    lines = [f"    <insight {attrs}>"]
    sum_chars = MAX_TEXT_CHARS if render_level == "full" else MAX_LIST_ITEM_CHARS
    lines.append(f"      <summary>{escape(_clip_text(insight.summary, sum_chars))}</summary>")
    if insight.proposed_action and render_level == "full":
        lines.append(
            f"      <proposed_action>{escape(_clip_text(insight.proposed_action, MAX_TEXT_CHARS))}</proposed_action>"
        )
    if render_level == "full":
        lines.extend(_render_capability_links(insight_links.get(insight.id, [])))
    lines.append("    </insight>")
    return lines


def _render_concept(concept: Any, *, render_level: str) -> list[str]:
    attrs = (
        f"id={quoteattr(concept.id)} "
        f"kind={quoteattr(concept.kind.value)} "
        f"confidence={quoteattr(f'{concept.confidence:.2f}')} "
        f"render_level={quoteattr(render_level)}"
    )
    lines = [f"    <concept {attrs}>"]
    lines.append(f"      <name>{escape(_clip_text(concept.name, MAX_TITLE_CHARS))}</name>")
    if render_level != "stub":
        def_chars = MAX_TEXT_CHARS if render_level == "full" else MAX_LIST_ITEM_CHARS
        lines.append(
            f"      <definition>{escape(_clip_text(concept.definition, def_chars))}</definition>"
        )
    lines.append("    </concept>")
    return lines


def _render_snapshot(snapshot: Any, *, render_level: str) -> list[str]:
    attrs = (
        f"id={quoteattr(snapshot.id)} "
        f"key={quoteattr(snapshot.snapshot_key)} "
        f"source={quoteattr(snapshot.source)} "
        f"total_rows={quoteattr(str(snapshot.total_rows))} "
        f"render_level={quoteattr(render_level)}"
    )
    lines = [f"    <snapshot {attrs}>"]
    lines.append(f"      <title>{escape(_clip_text(snapshot.title, MAX_TITLE_CHARS))}</title>")
    if snapshot.duckdb_path and render_level == "full":
        lines.append(
            f"      <duckdb_path>{escape(_clip_text(snapshot.duckdb_path, MAX_COMMAND_CHARS))}</duckdb_path>"
        )
    lines.append("    </snapshot>")
    return lines
