"""Build the Ollama prompt used by the semantic enforcement layer.

The semantic layer is generic: one prompt template feeds the rule
text + tool payload + recent trail to a local LLM and asks for a
yes/no violation verdict in strict JSON. Splitting prompt construction
into its own module keeps ``semantic_review.py`` focused on transport
and parsing.

The diff/payload is compacted to keep token use bounded — Ollama
locally is fast but unbounded payloads still hurt latency on large
``Write`` calls.
"""

from __future__ import annotations

import json
from typing import Any

_PAYLOAD_CHAR_BUDGET = 1200
_TRAIL_TAIL = 20


def _compact_payload(tool_input: dict[str, Any]) -> str:
    """Return a single-string compact representation of the tool input."""
    if not isinstance(tool_input, dict):
        return ""
    interesting = {
        k: tool_input[k]
        for k in (
            "file_path",
            "new_string",
            "old_string",
            "content",
            "command",
            "title",
            "decision_text",
            "rationale",
            "raw_text",
        )
        if k in tool_input
    }
    raw = json.dumps(interesting, ensure_ascii=False)
    if len(raw) <= _PAYLOAD_CHAR_BUDGET:
        return raw
    return raw[:_PAYLOAD_CHAR_BUDGET] + "...<truncated>"


def _compact_trail(trail: list[str]) -> str:
    tail = trail[-_TRAIL_TAIL:] if len(trail) > _TRAIL_TAIL else trail
    return ", ".join(tail) if tail else "(no prior tool calls)"


_TEMPLATE = """You are a strict policy reviewer. The agent is about to call a tool.
Decide whether the tool call violates the rule below. Respond with ONLY a JSON
object on a single line. No prose, no markdown, no leading text.

Rule:
{rule}

Tool the agent is about to call: {tool_name}
Tool payload (compacted): {payload}
Recent tool-call history (last {trail_n}): {trail}

Reply schema:
  {{"violates": true|false, "why": "<one short sentence>"}}

If unclear, default to "violates": false. False-positives block the agent's
work, so only flag a violation when the rule is clearly broken by the
described action."""


def build_review_prompt(
    *,
    rule_text: str,
    tool_name: str,
    tool_input: dict[str, Any],
    trail: list[str],
) -> str:
    """Render the strict-policy-reviewer prompt for one rule + tool call."""
    return _TEMPLATE.format(
        rule=rule_text.strip(),
        tool_name=tool_name,
        payload=_compact_payload(tool_input),
        trail_n=_TRAIL_TAIL,
        trail=_compact_trail(trail),
    )
