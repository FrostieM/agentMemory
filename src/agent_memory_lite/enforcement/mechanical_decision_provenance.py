"""Mechanical rule: ``memory_write_decision`` must carry provenance.

Detector tag (must appear in the rule's ``applies_to`` for the dispatcher
to route here): ``mechanical:decision-provenance``.

A decision without ``source_episode_id`` and without rationale or
bundled evidence is an unprovable architectural claim — exactly the
failure mode the operator flagged repeatedly: the agent ships an
opinion as a decision with no traceable evidence chain.

The check fires when NONE of the following provenance signals are
present in the payload:

  * ``source_episode_id`` — explicit link to a prior episode.
  * ``allow_orphan=true`` — deliberate operator-acknowledged orphan.
  * ``rationale`` ≥ 30 chars — direct ``memory_write_decision`` field.
  * ``decision_rationale`` ≥ 30 chars — ``memory_record_with_evidence``
    field (Move 2 atomic combo uses this name).
  * ``evidence_text`` or ``episode_raw_text`` — atomic combo writes a
    fresh episode in the same call, which IS the provenance.
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
_RATIONALE_FIELDS = ("rationale", "decision_rationale")
_BUNDLED_EVIDENCE_FIELDS = ("evidence_text", "episode_raw_text")


def _is_decision_write(tool_name: str) -> bool:
    return tool_name in _DECISION_WRITE_TOOLS


def _long_string_field(payload: dict[str, Any], names: tuple[str, ...]) -> bool:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and len(value.strip()) >= _MIN_RATIONALE_LEN:
            return True
    return False


def _has_provenance(payload: dict[str, Any]) -> bool:
    if payload.get("allow_orphan") is True:
        return True
    if payload.get("source_episode_id"):
        return True
    if _long_string_field(payload, _RATIONALE_FIELDS):
        return True
    # memory_record_with_evidence bundles a fresh episode in the same
    # call — its evidence/episode text IS the provenance.
    return _long_string_field(payload, _BUNDLED_EVIDENCE_FIELDS)


def _payload_title(payload: dict[str, Any]) -> str:
    return str(payload.get("title") or payload.get("decision_title") or "(no title)")


def detect_decision_without_provenance(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """Return diagnostic string if the call would write an unsourced decision."""
    if not _is_decision_write(tool_name):
        return None
    if not isinstance(tool_input, dict):
        return None
    if _has_provenance(tool_input):
        return None
    return (
        f"memory_write_decision titled {_payload_title(tool_input)!r} carries no "
        f"provenance: set source_episode_id from a recent memory_ingest_episode, "
        f"pass allow_orphan=true with a >={_MIN_RATIONALE_LEN}-char rationale, OR "
        f"use memory_record_with_evidence with evidence_text + decision_rationale"
    )
