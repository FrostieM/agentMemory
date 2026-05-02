"""Renderers for the ``<active_theories>`` section."""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from agent_memory_lite.models.capability_links import CapabilityLink
from agent_memory_lite.models.theories import Theory, TheoryEvidence
from agent_memory_lite.retrieval.context_builder_constants import (
    MAX_TEXT_CHARS,
    MAX_TITLE_CHARS,
)
from agent_memory_lite.retrieval.context_builder_models import TheoryContext
from agent_memory_lite.retrieval.context_builder_text import (
    _clip_text,
    _render_index_block,
    _render_string_items,
)


def _theory_attrs(item: Theory) -> str:
    return (
        f"id={quoteattr(item.id)} "
        f"status={quoteattr(item.status.value)} "
        f"domain={quoteattr(item.domain)} "
        f"confidence={quoteattr(f'{item.confidence:.2f}')} "
        f"importance={quoteattr(f'{item.importance:.2f}')} "
        f"evidence_count={quoteattr(str(item.evidence_count))} "
        f"evidence_strength={quoteattr(f'{item.evidence_strength:.2f}')} "
        f"source={quoteattr(item.source_episode_id or '')}"
    )


def _render_theory_evidence(items: list[TheoryEvidence]) -> list[str]:
    if not items:
        return []
    lines = ["      <evidence>"]
    for evidence in items:
        ev_attrs = (
            f"id={quoteattr(evidence.id)} "
            f"kind={quoteattr(evidence.kind.value)} "
            f"confidence={quoteattr(f'{evidence.confidence:.2f}')} "
            f"observed_at={quoteattr(evidence.observed_at)} "
            f"source={quoteattr(evidence.source_episode_id or '')}"
        )
        lines.append(
            f"        <item {ev_attrs}>{escape(_clip_text(evidence.summary, MAX_TEXT_CHARS))}</item>"
        )
    lines.append("      </evidence>")
    return lines


def _render_capability_links(items: list[CapabilityLink]) -> list[str]:
    if not items:
        return []
    lines = ["      <capability_links>"]
    for link in items:
        attrs = (
            f"id={quoteattr(link.id)} "
            f"capability_type={quoteattr(link.capability_type.value)} "
            f"capability_id={quoteattr(link.capability_id)} "
            f"relation={quoteattr(link.relation.value)} "
            f"strength={quoteattr(f'{link.strength:.2f}')} "
            f"source={quoteattr(link.source_episode_id or '')}"
        )
        lines.append(f"        <link {attrs}>")
        lines.append(
            f"          <name>{escape(_clip_text(link.capability_name, MAX_TITLE_CHARS))}</name>"
        )
        if link.rationale:
            lines.append(
                f"          <rationale>{escape(_clip_text(link.rationale, MAX_TEXT_CHARS))}</rationale>"
            )
        lines.append("        </link>")
    lines.append("      </capability_links>")
    return lines


def _render_theory(bundle: TheoryContext) -> list[str]:
    item = bundle.theory
    lines = [f"    <theory {_theory_attrs(item)}>"]
    lines.append(f"      <title>{escape(_clip_text(item.title, MAX_TITLE_CHARS))}</title>")
    lines.append(f"      <claim>{escape(_clip_text(item.claim, MAX_TEXT_CHARS))}</claim>")
    if item.mechanism:
        lines.append(
            f"      <mechanism>{escape(_clip_text(item.mechanism, MAX_TEXT_CHARS))}</mechanism>"
        )
    lines.extend(
        _render_string_items(
            container_tag="predictions",
            item_tag="item",
            items=item.predictions,
            indent="      ",
        )
    )
    lines.extend(
        _render_string_items(
            container_tag="validation_criteria",
            item_tag="item",
            items=item.validation_criteria,
            indent="      ",
        )
    )
    if item.experiment_plan:
        lines.append(
            f"      <experiment_plan>{escape(_clip_text(item.experiment_plan, MAX_TEXT_CHARS))}</experiment_plan>"
        )
    lines.extend(
        _render_string_items(
            container_tag="dependent_decisions",
            item_tag="decision_id",
            items=item.dependent_decision_ids,
            indent="      ",
        )
    )
    lines.extend(
        _render_string_items(
            container_tag="tags",
            item_tag="tag",
            items=item.tags,
            indent="      ",
        )
    )
    lines.extend(_render_theory_evidence(bundle.evidence))
    lines.extend(_render_capability_links(bundle.capability_links))
    lines.append("    </theory>")
    return lines


def _theory_index_extra(item: Theory) -> dict[str, str]:
    return {
        "status": item.status.value,
        "domain": item.domain or "",
        "updated": (item.updated_at or "")[:19],
    }


def _render_theories(
    items: list[TheoryContext],
    index_items: list[Theory] | None = None,
) -> list[str]:
    index_items = index_items or []
    if not items and not index_items:
        return ["  <active_theories/>"]
    lines = ["  <active_theories>"]
    for bundle in items:
        lines.extend(_render_theory(bundle))
    long_tail = [(t.id, t.title, _theory_index_extra(t)) for t in index_items]
    lines.extend(_render_index_block(full_count=len(items), long_tail=long_tail, indent="    "))
    lines.append("  </active_theories>")
    return lines
