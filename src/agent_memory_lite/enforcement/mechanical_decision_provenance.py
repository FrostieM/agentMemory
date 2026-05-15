"""Mechanical rule: ``memory_write_decision`` must carry provenance.

Detector tag (must appear in the rule's ``applies_to`` for the dispatcher
to route here): ``mechanical:decision-provenance``.

A decision without ``source_episode_id`` and without ``rationale`` is
an unprovable architectural claim — exactly the failure mode the
operator flagged repeatedly: the agent ships an opinion as a decision
with no traceable evidence chain.

The check fires when:

  * tool name is one of the decision-writing memory MCP tools, AND
  * the payload does NOT set ``source_episode_id``, AND
  * the payload does NOT set ``allow_orphan=true``, AND
  * the payload's ``rationale`` is empty or under 30 chars.

``allow_orphan=true`` is the deliberate escape for genuinely
no-episode decisions; we trust it as the agent's explicit signal.
"""

from __future__ import annotations

from typing import Any

RULE_TAG = "mechanical:decision-provenance"

_DECISION_WRITE_TOOLS = frozenset(
    {
        "memory_write_decision",
        "mcp__agent-memory-lite__memory_write_decision",
        "memory_record_with_evidence",
        "mcp__agent-memory-lite__memory_record_with_evidence",
    }
)

_MIN_RATIONALE_LEN = 30


def _is_decision_write(tool_name: str) -> bool:
    return tool_name in _DECISION_WRITE_TOOLS


def _has_provenance(payload: dict[str, Any]) -> bool:
    if payload.get("allow_orphan") is True:
        return True
    if payload.get("source_episode_id"):
        return True
    rationale = payload.get("rationale")
    return isinstance(rationale, str) and len(rationale.strip()) >= _MIN_RATIONALE_LEN


def detect_decision_without_provenance(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """Return diagnostic string if the call would write an unsourced decision."""
    if not _is_decision_write(tool_name):
        return None
    if not isinstance(tool_input, dict):
        return None
    if _has_provenance(tool_input):
        return None
    title = tool_input.get("title") or "(no title)"
    return (
        f"memory_write_decision titled {title!r} carries no provenance: "
        f"set source_episode_id from a recent memory_ingest_episode, OR pass "
        f"allow_orphan=true with a >={_MIN_RATIONALE_LEN}-char rationale"
    )
