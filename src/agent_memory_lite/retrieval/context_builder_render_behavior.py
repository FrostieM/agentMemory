"""Renderer for the ``<behavior_instructions>`` section."""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from agent_memory_lite.models.behavior import BehaviorInstruction, BehaviorInstructionSet
from agent_memory_lite.retrieval.context_builder_constants import (
    MAX_TEXT_CHARS,
    MAX_TITLE_CHARS,
)
from agent_memory_lite.retrieval.context_builder_text import (
    _clip_text,
    _render_index_block,
    _render_string_items,
)


def _render_behavior_instruction(item: BehaviorInstruction) -> list[str]:
    attrs = (
        f"id={quoteattr(item.id)} "
        f"kind={quoteattr(item.kind.value)} "
        f"scope={quoteattr(item.scope.value)} "
        f"priority={quoteattr(item.priority.value)} "
        f"conflict_policy={quoteattr(item.conflict_policy.value)} "
        f"confidence={quoteattr(f'{item.confidence:.2f}')} "
        f"source={quoteattr(item.source_episode_id or '')} "
        f"source_type={quoteattr(item.source_type)} "
        f"source_id={quoteattr(item.source_id or '')}"
    )
    lines = [f"    <instruction {attrs}>"]
    lines.append(f"      <name>{escape(_clip_text(item.name, MAX_TITLE_CHARS))}</name>")
    lines.append(f"      <rule>{escape(_clip_text(item.rule, MAX_TEXT_CHARS))}</rule>")
    if item.rationale:
        lines.append(
            f"      <rationale>{escape(_clip_text(item.rationale, MAX_TEXT_CHARS))}</rationale>"
        )
    if item.reviewed_at or item.expires_at or item.conflict_group:
        governance_attrs = (
            f"reviewed_at={quoteattr(item.reviewed_at or '')} "
            f"expires_at={quoteattr(item.expires_at or '')} "
            f"conflict_group={quoteattr(item.conflict_group or '')} "
            f"application_count={quoteattr(str(item.application_count))}"
        )
        lines.append(f"      <governance {governance_attrs}/>")
    lines.extend(
        _render_string_items(
            container_tag="applies_to",
            item_tag="item",
            items=item.applies_to,
            indent="      ",
        )
    )
    lines.append("    </instruction>")
    return lines


def _behavior_index_extra(item: BehaviorInstruction) -> dict[str, str]:
    return {
        "kind": item.kind.value,
        "scope": item.scope.value,
        "updated": (item.updated_at or "")[:19],
    }


def _render_behavior_instructions(
    items: BehaviorInstructionSet | None,
    index_items: list[BehaviorInstruction] | None = None,
) -> list[str]:
    index_items = index_items or []
    instructions = list(items.instructions) if items else []
    if not instructions and not index_items:
        return ["  <behavior_instructions/>"]
    lines = ["  <behavior_instructions>"]
    for instruction in instructions:
        lines.extend(_render_behavior_instruction(instruction))
    long_tail = [(it.id, it.name, _behavior_index_extra(it)) for it in index_items]
    lines.extend(
        _render_index_block(full_count=len(instructions), long_tail=long_tail, indent="    ")
    )
    lines.append("  </behavior_instructions>")
    return lines
