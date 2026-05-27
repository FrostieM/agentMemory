"""Mechanical trail-aware rule: ``memory_impact_check`` before ``Edit``.

Detector tag (must appear in the rule's ``applies_to``):
``mechanical:read-before-edit``.

Catches the "agent patches by fragment without checking file impact"
failure mode. Behavior_instruction
``Memory-first before reading or editing source`` already lives in
the foreground envelope, but the operator observed the agent skipping
it under pressure. This trail-aware detector blocks the ``Edit`` tool
call when no ``memory_impact_check`` of the target
file appeared in the session trail.

The check is per-file: impact-check of ``a.py`` does not authorize ``Edit``
of ``b.py``. We do not track full file_paths in the trail (cost), so
the check is approximate: if ANY ``memory_impact_check``
appears in the trail we let the Edit through. Conservative — better
to under-block than over-block on a noisy session.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.enforcement.session_trail import has_called

RULE_TAG = "mechanical:read-before-edit"

_TARGET_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_PRIOR_TOOLS = ("memory_impact_check",)


def detect_edit_without_read(
    tool_name: str,
    tool_input: dict[str, Any],
    trail: list[str],
) -> str | None:
    """Return diagnostic if Edit/Write fires without any prior impact check.

    The detector is conservative: any prior ``memory_impact_check`` in the
    session counts as evidence that the agent loaded impact context.
    """
    if tool_name not in _TARGET_TOOLS:
        return None
    if not isinstance(tool_input, dict):
        return None
    if has_called(trail, *_PRIOR_TOOLS):
        return None
    file_path = tool_input.get("file_path") or "(unknown)"
    return (
        f"{tool_name} on {file_path} fires without prior memory_impact_check "
        f"in this session; the agent must load file impact context before "
        f"mutating it"
    )
