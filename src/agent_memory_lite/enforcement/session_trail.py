"""Read Claude Code transcript JSONL for prior tool-call evidence.

Semantic rules (read-before-edit, search-before-architectural-write)
need to know what the agent already did THIS SESSION. Claude Code
writes one JSONL row per assistant/user/tool message into
``transcript_path``; this module extracts the set of tool names that
appeared in assistant turns within a configurable lookback window.

Failure-soft: any IO / parse error returns an empty trail so the hook
falls through to "no prior evidence" rather than crashing the agent's
turn. Callers should treat empty-trail as "no prior call observed"
when deciding whether a rule fires.
"""

from __future__ import annotations

import json
from pathlib import Path

_DEFAULT_LOOKBACK_LINES = 400


def _iter_tail(path: Path, max_lines: int) -> list[str]:
    """Return the last ``max_lines`` lines from ``path``, best effort."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    return lines[-max_lines:] if len(lines) > max_lines else lines


def _extract_tool_names_from_message(payload: object) -> list[str]:
    """Pull tool_use ``name`` values out of one transcript message."""
    if not isinstance(payload, dict):
        return []
    message = payload.get("message")
    if not isinstance(message, dict):
        return []
    if message.get("role") != "assistant":
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    names: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def read_prior_tool_calls(
    transcript_path: str | None,
    *,
    max_lines: int = _DEFAULT_LOOKBACK_LINES,
) -> list[str]:
    """Return tool names called in assistant turns within the lookback window.

    Order preserved; same tool called twice appears twice. Empty list on
    any failure (missing transcript path, IO error, malformed JSON).
    """
    if not transcript_path:
        return []
    path = Path(transcript_path)
    if not path.exists():
        return []
    names: list[str] = []
    for raw in _iter_tail(path, max_lines):
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        names.extend(_extract_tool_names_from_message(payload))
    return names


def has_called(trail: list[str], *candidates: str) -> bool:
    """Did any of the ``candidates`` tool names appear in the trail?

    Accepts both bare names (``Read``) and MCP-prefixed names
    (``mcp__agent-memory-lite__memory_file_digest``); matches by
    suffix so callers don't need to know the prefix shape.
    """
    if not candidates:
        return False
    needles = tuple(name for name in candidates if name)
    for actual in trail:
        if any(actual == needle or actual.endswith(needle) for needle in needles):
            return True
    return False
