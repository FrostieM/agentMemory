"""Strip MCP function-call markup that leaked into a parameter value.

When an agent invokes a memory_* tool via MCP and accidentally embeds the
function-call boundary tags (`</decision_text>`, `<parameter name="...">`,
`</invoke>`, etc.) inside the textual content of a parameter, that markup
ends up persisted verbatim in SQLite. UI then renders garbage at the tail
of the field.

This module truncates a text payload at the FIRST occurrence of any
strict MCP marker. The pre-truncation prefix is the legitimate content
the operator intended; everything after is the leaked function-call
boundary plus next-parameter content.

For ``decision_text`` we also expose ``extract_rationale`` so a route can
recover the rationale that leaked into the decision_text field.

Strict markers only — generic angle brackets (`<repo>`, `<=`, `<name>`,
etc.) are operator-legitimate and never touched.
"""

from __future__ import annotations

# Strict MCP function-call markers. Any of these inside a text payload
# means the agent leaked structural markup; everything from here on out
# is unsafe and must be dropped.
_MARKERS = (
    "</decision_text>",
    "</raw_text>",
    "</parameter>",
    "</invoke>",
    '<parameter name="',
)


def strip_mcp_markup(text: str | None) -> str | None:
    """Return text truncated at the first MCP marker, or unchanged if none.

    Idempotent: clean text passes through untouched.
    """
    if not text:
        return text
    earliest = -1
    for marker in _MARKERS:
        pos = text.find(marker)
        if pos >= 0 and (earliest < 0 or pos < earliest):
            earliest = pos
    if earliest < 0:
        return text
    return text[:earliest].rstrip()


def has_mcp_markup(text: str | None) -> bool:
    """True iff `text` contains any strict MCP marker."""
    if not text:
        return False
    return any(marker in text for marker in _MARKERS)


def extract_rationale(decision_text: str | None) -> str | None:
    """Pull rationale content from leaked `<parameter name="rationale">`.

    Used by the ``/memory/write_decision`` route to recover rationale
    that leaked into ``decision_text`` when the agent's MCP invocation
    accidentally embedded the parameter boundary inside the value.
    Returns the inner text (stripped) or None if no rationale block.
    """
    if not decision_text:
        return None
    marker = '<parameter name="rationale">'
    start = decision_text.find(marker)
    if start < 0:
        return None
    body_start = start + len(marker)
    end_markers = ("</parameter>", "</invoke>")
    end_positions = [decision_text.find(m, body_start) for m in end_markers]
    found = [p for p in end_positions if p >= 0]
    body_end = min(found) if found else len(decision_text)
    extracted = decision_text[body_start:body_end].strip()
    return extracted or None
