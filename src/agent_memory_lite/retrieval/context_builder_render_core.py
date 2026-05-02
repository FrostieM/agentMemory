"""Renderers for ``<core_memory>``, ``<task_state>``, and ``<active_decisions>``."""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from agent_memory_lite.models.core_memory import CoreMemory
from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.task_state import TaskState
from agent_memory_lite.retrieval.context_builder_constants import (
    MAX_FULL_DECISION_ITEMS,
    MAX_TEXT_CHARS,
    MAX_TITLE_CHARS,
)
from agent_memory_lite.retrieval.context_builder_text import (
    _clip_text,
    _limited_items,
    _render_index_block,
    _render_omitted_line,
)
from agent_memory_lite.utils.text_encoding import repair_common_mojibake


def _render_core(items: list[CoreMemory]) -> list[str]:
    if not items:
        return ["  <core_memory/>"]
    lines = ["  <core_memory>"]
    for item in items:
        attrs = (
            f"key={quoteattr(item.key)} "
            f"confidence={quoteattr(f'{item.confidence:.2f}')} "
            f"source={quoteattr(item.source_episode_id or '')}"
        )
        lines.append(f"    <item {attrs}>{escape(item.value)}</item>")
    lines.append("  </core_memory>")
    return lines


def _render_task(task: TaskState | None) -> list[str]:
    if task is None:
        return ["  <task_state/>"]
    lines = [f"  <task_state task_id={quoteattr(task.task_id)}>"]
    lines.append(f"    <goal>{escape(_clip_text(task.goal, MAX_TEXT_CHARS))}</goal>")
    lines.append(f"    <status>{escape(task.status)}</status>")
    if task.next_action:
        lines.append(
            f"    <next_action>{escape(_clip_text(task.next_action, MAX_TEXT_CHARS))}</next_action>"
        )
    if task.blockers:
        lines.append("    <blockers>")
        visible, omitted = _limited_items(task.blockers)
        for item in visible:
            lines.append(f"      <item>{escape(item)}</item>")
        lines.extend(_render_omitted_line(count=omitted, tag="omitted", indent="      "))
        lines.append("    </blockers>")
    lines.append("  </task_state>")
    return lines


def _decision_index_extra(item: Decision) -> dict[str, str]:
    return {
        "status": item.status.value if item.status else "",
        "updated": (item.updated_at or "")[:19],
    }


def _render_decisions(
    items: list[Decision],
    index_items: list[Decision] | None = None,
) -> list[str]:
    index_items = index_items or []
    if not items and not index_items:
        return ["  <active_decisions/>"]
    lines = ["  <active_decisions>"]
    for index, item in enumerate(items):
        full_text = index < MAX_FULL_DECISION_ITEMS
        attrs = (
            f"id={quoteattr(item.id)} "
            f"confidence={quoteattr(f'{item.confidence:.2f}')} "
            f"source={quoteattr(item.source_episode_id or '')} "
            f"full_text={quoteattr(str(full_text).lower())}"
        )
        lines.append(f"    <decision {attrs}>")
        lines.append(f"      <title>{escape(_clip_text(item.title, MAX_TITLE_CHARS))}</title>")
        decision_text = (
            repair_common_mojibake(item.decision_text)
            if full_text
            else _clip_text(item.decision_text, MAX_TEXT_CHARS)
        )
        lines.append(f"      <text>{escape(decision_text)}</text>")
        if item.rationale:
            rationale = (
                repair_common_mojibake(item.rationale)
                if full_text
                else _clip_text(item.rationale, MAX_TEXT_CHARS)
            )
            lines.append(f"      <rationale>{escape(rationale)}</rationale>")
        lines.append("    </decision>")
    long_tail = [(it.id, it.title, _decision_index_extra(it)) for it in index_items]
    lines.extend(_render_index_block(full_count=len(items), long_tail=long_tail, indent="    "))
    lines.append("  </active_decisions>")
    return lines
