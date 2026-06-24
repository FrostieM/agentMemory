"""Phase 4 helper: precondition resolution and advisory text for reflex rules.

Extracted from ``reflex_check`` to keep that module small. Maps precondition
kinds to the tools that satisfy them, checks the session trail for evidence,
and renders the human-readable failure message.
"""

from __future__ import annotations

from agent_memory_lite.enforcement.session_trail import has_called

# Precondition kind → tools that satisfy it.
_PRECONDITION_TOOL_MAP: dict[str, tuple[str, ...]] = {
    "impact_check_within_seconds": ("memory_impact_check",),
    "memory_search_within_seconds": ("memory_search",),
    "playbook_fetch": ("memory_invoke_skill",),
}


def _precondition_satisfied(*, precondition_kind: str, trail: list[str]) -> bool:
    """True if the trail contains evidence that the precondition was met."""
    tools = _PRECONDITION_TOOL_MAP.get(precondition_kind)
    if not tools:
        # Unknown kind -- safest default is to skip the check entirely
        # so a typo in operator-seeded rule doesn't lock the agent out.
        return True
    return has_called(trail, *tools)


def _advisory_text(rule_name: str, precondition_kind: str, params: dict[str, object]) -> str:
    """Human-readable failure message for the diagnostic line."""
    window = params.get("window_seconds")
    window_clause = f" within {window}s" if window else ""
    fallback_tool = ", ".join(_PRECONDITION_TOOL_MAP.get(precondition_kind, ("(unknown)",)))
    return (
        f"reflex {rule_name!r}: precondition {precondition_kind!r}"
        f"{window_clause} not satisfied. Call {fallback_tool} first, "
        f"then retry this tool."
    )
