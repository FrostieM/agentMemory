"""Mechanical trail-aware rule: ``memory_search`` before architectural writes.

Detector tag: ``mechanical:search-before-arch``.

Catches "agent writes a fresh decision/theory without checking prior
art" — the failure mode that lets the agent re-open settled
architecture or contradict an existing decision because they never
ran ``memory_list_decisions`` or ``memory_search`` for the topic.

Fires on ``memory_write_decision`` / ``memory_write_theory`` /
``memory_record_with_evidence`` when no search-flavored tool appears
in the session trail.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.enforcement.session_trail import has_called

RULE_TAG = "mechanical:search-before-arch"

_TARGET_TOOLS = frozenset(
    {
        "memory_write_decision",
        "mcp__agent-memory-lite__memory_write_decision",
        "memory_write_theory",
        "mcp__agent-memory-lite__memory_write_theory",
        "memory_record_with_evidence",
        "mcp__agent-memory-lite__memory_record_with_evidence",
    }
)
_PRIOR_TOOLS = (
    "memory_search",
    "memory_list_decisions",
    "memory_list_theories",
    "memory_get_context",
)


def detect_arch_write_without_search(
    tool_name: str,
    tool_input: dict[str, Any],
    trail: list[str],
) -> str | None:
    """Return diagnostic if an architectural write fires without prior search."""
    if tool_name not in _TARGET_TOOLS:
        return None
    if not isinstance(tool_input, dict):
        return None
    if has_called(trail, *_PRIOR_TOOLS):
        return None
    title = tool_input.get("title") or tool_input.get("decision_title") or "(no title)"
    return (
        f"{tool_name} titled {title!r} fires without prior memory_search "
        f"or memory_list_decisions/theories; the agent must check for "
        f"prior art before writing new architecture"
    )
