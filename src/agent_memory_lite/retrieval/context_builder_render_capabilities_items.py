"""Per-item renderers for ``<agent_capabilities>``.

Each capability kind (role, skill, playbook) follows the same shape:
attrs + name + (purpose|summary|goal) + (responsibilities|when_to_use|
steps). Pulled out of the section orchestrator so the orchestrator
itself can stay short.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from agent_memory_lite.models.capabilities import AgentPlaybook, AgentRole, AgentSkill
from agent_memory_lite.retrieval.context_builder_constants import (
    MAX_LIST_ITEM_CHARS,
    MAX_TEXT_CHARS,
    MAX_TITLE_CHARS,
)
from agent_memory_lite.retrieval.context_builder_text import _clip_text


def _capability_attrs(
    *,
    item_id: str,
    confidence: float,
    source_episode_id: str | None,
) -> str:
    return (
        f"id={quoteattr(item_id)} "
        f"confidence={quoteattr(f'{confidence:.2f}')} "
        f"source={quoteattr(source_episode_id or '')}"
    )


def _render_item_list(
    *,
    container_tag: str,
    item_tag: str,
    items: list[str],
    indent: str = "      ",
) -> list[str]:
    if not items:
        return []
    lines = [f"{indent}<{container_tag}>"]
    for item in items:
        lines.append(f"{indent}  <{item_tag}>{escape(item)}</{item_tag}>")
    lines.append(f"{indent}</{container_tag}>")
    return lines


def _summary_chars(render_level: str) -> int:
    return MAX_TEXT_CHARS if render_level == "full" else MAX_LIST_ITEM_CHARS


def _render_capability_item(
    *,
    item: AgentRole | AgentSkill | AgentPlaybook,
    item_tag: str,
    summary_tag: str,
    summary_text: str,
    list_container: str,
    list_item_tag: str,
    list_items: list[str],
    render_level: str,
) -> list[str]:
    attrs = (
        _capability_attrs(
            item_id=item.id,
            confidence=item.confidence,
            source_episode_id=item.source_episode_id,
        )
        + f" render_level={quoteattr(render_level)}"
    )
    lines = [f"    <{item_tag} {attrs}>"]
    lines.append(f"      <name>{escape(_clip_text(item.name, MAX_TITLE_CHARS))}</name>")
    if render_level != "stub":
        body = _clip_text(summary_text, _summary_chars(render_level))
        lines.append(f"      <{summary_tag}>{escape(body)}</{summary_tag}>")
    if render_level == "full":
        lines.extend(
            _render_item_list(
                container_tag=list_container,
                item_tag=list_item_tag,
                items=list_items,
            )
        )
    lines.append(f"    </{item_tag}>")
    return lines


def _render_role(role: AgentRole, *, render_level: str) -> list[str]:
    return _render_capability_item(
        item=role,
        item_tag="role",
        summary_tag="purpose",
        summary_text=role.purpose,
        list_container="responsibilities",
        list_item_tag="item",
        list_items=role.responsibilities,
        render_level=render_level,
    )


def _render_skill(skill: AgentSkill, *, render_level: str) -> list[str]:
    return _render_capability_item(
        item=skill,
        item_tag="skill",
        summary_tag="summary",
        summary_text=skill.summary,
        list_container="when_to_use",
        list_item_tag="item",
        list_items=skill.when_to_use,
        render_level=render_level,
    )


def _render_playbook(playbook: AgentPlaybook, *, render_level: str) -> list[str]:
    return _render_capability_item(
        item=playbook,
        item_tag="playbook",
        summary_tag="goal",
        summary_text=playbook.goal,
        list_container="steps",
        list_item_tag="step",
        list_items=playbook.steps,
        render_level=render_level,
    )
