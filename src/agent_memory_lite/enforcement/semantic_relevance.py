"""Filter semantic rules by relevance to the current tool call.

A pinned rule about ``git commit`` discipline should not pay an Ollama
call when the agent is doing an ``Edit``. The filter prunes obvious
mismatches before we hit the LLM judge.

Heuristics, in order:

  1. Response-bound rules (``applies_to`` carries tokens like
     ``response`` / ``first sentence`` / ``verbatim``) skip ALL tool
     calls — PreToolUse never sees the response.
  2. Rules whose ``applies_to`` matches a tool-name synonym for the
     current call are included.
  3. Empty ``applies_to`` is treated as "apply everywhere" — the
     conservative default; the LLM judge handles false-positive
     filtering inside its strict-policy prompt.

The goal is bounded Ollama cost (typically ≤3 rules per tool call)
without hiding rules the operator pinned for enforcement.
"""

from __future__ import annotations

from agent_memory_lite.enforcement.rule_loader import EnforcementRule

_RESPONSE_ONLY_TOKENS = (
    "response",
    "first sentence",
    "before answering",
    "verbatim",
    "acknowledg",
)

_TOOL_SYNONYMS: dict[str, tuple[str, ...]] = {
    "Edit": ("edit", "editing", "modify", "patch", "code"),
    "Write": ("write", "writ", "creating", "new file", "code"),
    "NotebookEdit": ("edit", "notebook", "code"),
    "Read": ("read", "reading"),
    "Grep": ("grep", "search"),
    "Glob": ("glob", "search"),
    "Bash": ("bash", "shell", "git", "commit", "push", "deploy", "ship", "ci"),
}

_MEMORY_TOOL_KEYWORDS = (
    "memory_",
    "decision",
    "theory",
    "experiment",
    "evidence",
    "capability",
    "link_capability",
    "candidate",
    "insight",
    "concept",
)


def _is_response_only(applies_to_lower: list[str]) -> bool:
    return any(token in entry for entry in applies_to_lower for token in _RESPONSE_ONLY_TOKENS)


def _matches_tool(applies_to_lower: list[str], tool_name: str) -> bool:
    tool_lower = tool_name.lower()
    # Direct substring match on the tool name itself first.
    if any(tool_lower in entry for entry in applies_to_lower):
        return True
    synonyms = _TOOL_SYNONYMS.get(tool_name, ())
    if synonyms and any(syn in entry for entry in applies_to_lower for syn in synonyms):
        return True
    # Memory-write tools: name carries the keyword.
    if not (tool_name.startswith("memory_") or "memory_" in tool_lower):
        return False
    return any(kw in entry for entry in applies_to_lower for kw in _MEMORY_TOOL_KEYWORDS)


def is_rule_relevant(rule: EnforcementRule, tool_name: str) -> bool:
    """Return True if this rule should be sent to the LLM judge.

    Empty ``applies_to`` → always relevant (conservative). Otherwise:
    response-only rules are skipped; rules with tool-name overlap are
    kept; everything else is kept by default (the LLM handles the
    final say).
    """
    if not rule.applies_to:
        return True
    lower = [entry.lower() for entry in rule.applies_to]
    return not (_is_response_only(lower) and not _matches_tool(lower, tool_name))


def filter_relevant(rules: list[EnforcementRule], *, tool_name: str) -> list[EnforcementRule]:
    """Apply :func:`is_rule_relevant` to a rule batch."""
    return [r for r in rules if is_rule_relevant(r, tool_name)]
