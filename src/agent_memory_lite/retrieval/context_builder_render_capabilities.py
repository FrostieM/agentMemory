"""Renderer for the ``<agent_capabilities>`` section.

Per-item renderers live in
``context_builder_render_capabilities_items.py``; this module just
walks the role / skill / playbook lists and emits the ``<index>``
blocks for the long tail.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from agent_memory_lite.models.capabilities import (
    AgentCapabilities,
    AgentPlaybook,
    AgentRole,
    AgentSkill,
)
from agent_memory_lite.retrieval.context_builder_constants import MAX_TEXT_CHARS
from agent_memory_lite.retrieval.context_builder_render_capabilities_items import (
    _render_playbook,
    _render_role,
    _render_skill,
)
from agent_memory_lite.retrieval.context_builder_text import _clip_text, _render_index_block


def _capability_index_extra(kind: str) -> dict[str, str]:
    return {"kind": kind}


def _capability_index_block(
    *,
    kind: str,
    full_count: int,
    items: list[AgentRole] | list[AgentSkill] | list[AgentPlaybook],
) -> list[str]:
    if not items:
        return []
    long_tail = [(it.id, it.name, _capability_index_extra(kind)) for it in items]
    return _render_index_block(full_count=full_count, long_tail=long_tail, indent="    ")


def _render_agent_capabilities(
    capabilities: AgentCapabilities | None,
    *,
    render_level: str = "full",
    why_relevant: str = "",
    index_roles: list[AgentRole] | None = None,
    index_skills: list[AgentSkill] | None = None,
    index_playbooks: list[AgentPlaybook] | None = None,
) -> list[str]:
    index_roles = index_roles or []
    index_skills = index_skills or []
    index_playbooks = index_playbooks or []
    has_full = capabilities is not None and (
        capabilities.roles or capabilities.skills or capabilities.playbooks
    )
    has_index = bool(index_roles or index_skills or index_playbooks)
    if not has_full and not has_index:
        return ["  <agent_capabilities/>"]

    lines = ["  <agent_capabilities>"]
    if why_relevant:
        lines.append(
            f"    <why_relevant>{escape(_clip_text(why_relevant, MAX_TEXT_CHARS))}</why_relevant>"
        )
    if capabilities is not None:
        for role in capabilities.roles:
            lines.extend(_render_role(role, render_level=render_level))
        for skill in capabilities.skills:
            lines.extend(_render_skill(skill, render_level=render_level))
        for playbook in capabilities.playbooks:
            lines.extend(_render_playbook(playbook, render_level=render_level))

    lines.extend(
        _capability_index_block(
            kind="role",
            full_count=len(capabilities.roles) if capabilities else 0,
            items=index_roles,
        )
    )
    lines.extend(
        _capability_index_block(
            kind="skill",
            full_count=len(capabilities.skills) if capabilities else 0,
            items=index_skills,
        )
    )
    lines.extend(
        _capability_index_block(
            kind="playbook",
            full_count=len(capabilities.playbooks) if capabilities else 0,
            items=index_playbooks,
        )
    )
    lines.append("  </agent_capabilities>")
    return lines
