"""Mechanical trail-aware rule: ``memory_impact_check`` before ``Read`` / ``Grep``.

Detector tag (must appear in the rule's ``applies_to``):
``mechanical:impact-check-before-read``.

Sibling to ``mechanical_read_before_edit`` which covers
Edit / Write / NotebookEdit. This module closes the discipline-rule-1
coverage gap for the read-side tools (Read, Grep) so the agent must
load file impact context BEFORE inspecting source the same way it must
before mutating it.

The check is conservative (same shape as the edit-side rule): ANY
``memory_impact_check`` call in the session trail satisfies the rule.
We do not track file_paths in the trail, so one impact_check authorises
every subsequent Read / Grep in the session -- better to under-block
than over-block on a noisy session.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.enforcement.session_trail import has_called

RULE_TAG = "mechanical:impact-check-before-read"

_TARGET_TOOLS = frozenset({"Read", "Grep"})
_PRIOR_TOOLS = ("memory_impact_check",)


def detect_read_without_impact_check(
    tool_name: str,
    tool_input: dict[str, Any],
    trail: list[str],
) -> str | None:
    """Return diagnostic if Read / Grep fires without any prior impact check.

    The detector is conservative: any prior ``memory_impact_check`` in the
    session counts as evidence that the agent loaded impact context.
    Read uses ``file_path``; Grep uses ``pattern`` (with optional ``path``);
    either falls back to ``(unknown)`` so the diagnostic still cites the
    tool name.
    """
    if tool_name not in _TARGET_TOOLS:
        return None
    if not isinstance(tool_input, dict):
        return None
    if has_called(trail, *_PRIOR_TOOLS):
        return None
    target = (
        tool_input.get("file_path")
        or tool_input.get("path")
        or tool_input.get("pattern")
        or "(unknown)"
    )
    return (
        f"{tool_name} on {target} fires without prior memory_impact_check "
        f"in this session; the agent must load file impact context (call "
        f"memory_impact_check(file_path=<path>) first) before reading source "
        f"files. Read is the fallback for understanding algorithm logic; "
        f"impact_check is the default."
    )
