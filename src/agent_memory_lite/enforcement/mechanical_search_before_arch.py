"""Mechanical trail-aware rule: ``memory_search`` before architectural writes.

Detector tag: ``mechanical:search-before-arch``.

Catches "agent writes a fresh decision/theory without checking prior
art" -- the failure mode that lets the agent re-open settled
architecture or contradict an existing decision because they never
ran ``memory_search`` for the topic.

Fires on v3 ``memory_write`` calls with ``kind=decision`` or
``kind=theory`` when no search-flavored tool appears in the session
trail.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.enforcement.session_trail import has_called

RULE_TAG = "mechanical:search-before-arch"

_ARCH_KINDS = {"decision", "theory"}
_PRIOR_TOOLS = ("memory_search",)


def _is_memory_write(tool_name: str) -> bool:
    return tool_name == "memory_write" or tool_name.endswith("__memory_write")


def _write_kind(tool_input: dict[str, Any]) -> str:
    kind = tool_input.get("kind")
    return kind.lower() if isinstance(kind, str) else ""


def _payload_title(tool_input: dict[str, Any]) -> str:
    payload = tool_input.get("payload")
    payload_dict = payload if isinstance(payload, dict) else {}
    title = (
        payload_dict.get("title")
        or payload_dict.get("decision_title")
        or payload_dict.get("claim")
        or tool_input.get("title")
        or "(no title)"
    )
    return str(title)


def detect_arch_write_without_search(
    tool_name: str,
    tool_input: dict[str, Any],
    trail: list[str],
) -> str | None:
    """Return diagnostic if an architectural write fires without prior search."""
    if not isinstance(tool_input, dict):
        return None
    if not _is_memory_write(tool_name) or _write_kind(tool_input) not in _ARCH_KINDS:
        return None
    if has_called(trail, *_PRIOR_TOOLS):
        return None
    title = _payload_title(tool_input)
    return (
        f"{tool_name} titled {title!r} fires without prior memory_search "
        f"for the topic; the agent must check for prior art before writing "
        f"new architecture"
    )
