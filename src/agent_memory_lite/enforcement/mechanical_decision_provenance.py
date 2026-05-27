"""Mechanical rule: ``memory_write(kind=decision)`` must carry provenance.

Detector tag (must appear in the rule's ``applies_to`` for the dispatcher
to route here): ``mechanical:decision-provenance``.

A decision without ``source_episode_id`` and without rationale or
bundled evidence is an unprovable architectural claim -- exactly the
failure mode the operator flagged repeatedly: the agent ships an
opinion as a decision with no traceable evidence chain.

The check fires when NONE of the following provenance signals are
present in the payload:

  * ``source_episode_id`` -- explicit link to a prior episode.
  * ``allow_orphan=true`` -- deliberate operator-acknowledged orphan.
  * ``rationale`` >= 30 chars -- decision payload rationale.
"""

from __future__ import annotations

from typing import Any

RULE_TAG = "mechanical:decision-provenance"

_MIN_RATIONALE_LEN = 30
_RATIONALE_FIELDS = ("rationale",)


def _is_memory_write(tool_name: str) -> bool:
    return tool_name == "memory_write" or tool_name.endswith("__memory_write")


def _is_decision_write(tool_name: str, tool_input: dict[str, Any]) -> bool:
    kind = tool_input.get("kind")
    return _is_memory_write(tool_name) and isinstance(kind, str) and kind.lower() == "decision"


def _payload(tool_input: dict[str, Any]) -> dict[str, Any]:
    payload = tool_input.get("payload")
    return payload if isinstance(payload, dict) else tool_input


def _long_string_field(payload: dict[str, Any], names: tuple[str, ...]) -> bool:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and len(value.strip()) >= _MIN_RATIONALE_LEN:
            return True
    return False


def _has_provenance(tool_input: dict[str, Any]) -> bool:
    payload = _payload(tool_input)
    if tool_input.get("allow_orphan") is True or payload.get("allow_orphan") is True:
        return True
    if tool_input.get("source_episode_id") or payload.get("source_episode_id"):
        return True
    return _long_string_field(payload, _RATIONALE_FIELDS)


def _payload_title(tool_input: dict[str, Any]) -> str:
    payload = _payload(tool_input)
    return str(payload.get("title") or payload.get("decision_title") or "(no title)")


def detect_decision_without_provenance(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """Return diagnostic string if the call would write an unsourced decision."""
    if not isinstance(tool_input, dict):
        return None
    if not _is_decision_write(tool_name, tool_input):
        return None
    if _has_provenance(tool_input):
        return None
    return (
        f"memory_write(kind=decision) titled {_payload_title(tool_input)!r} carries no "
        f"provenance: set source_episode_id from a recent memory_write(kind=episode), "
        f"pass allow_orphan=true with a >={_MIN_RATIONALE_LEN}-char rationale, OR "
        f"pass allow_orphan=true only for a deliberate historical decision"
    )
