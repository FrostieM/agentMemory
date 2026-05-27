"""Mechanical layer orchestrator.

Routes each active mechanical rule to its detector. A rule opts into a
detector by listing the detector's ``RULE_TAG`` inside its
``applies_to`` array (alongside the ``enforcement:mechanical`` tag that
made the rule visible to ``rule_loader``).

The dispatcher iterates rules; for each rule it picks the first
matching detector tag and runs it. Detectors are deterministic and
fast — running all five against an Edit payload is sub-millisecond.

A detector returns ``None`` (no violation) or a diagnostic string
(violation). Violations are wrapped in ``RuleViolation`` carrying the
rule's id and name so the agent can cite it back in the retry.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_memory_lite.enforcement.mechanical_decision_provenance import (
    RULE_TAG as DECISION_PROVENANCE_TAG,
)
from agent_memory_lite.enforcement.mechanical_decision_provenance import (
    detect_decision_without_provenance,
)
from agent_memory_lite.enforcement.mechanical_impact_check_before_read import (
    RULE_TAG as IMPACT_CHECK_BEFORE_READ_TAG,
)
from agent_memory_lite.enforcement.mechanical_impact_check_before_read import (
    detect_read_without_impact_check,
)
from agent_memory_lite.enforcement.mechanical_magic_number import (
    RULE_TAG as MAGIC_NUMBER_TAG,
)
from agent_memory_lite.enforcement.mechanical_magic_number import (
    detect_magic_number,
)
from agent_memory_lite.enforcement.mechanical_read_before_edit import (
    RULE_TAG as READ_BEFORE_EDIT_TAG,
)
from agent_memory_lite.enforcement.mechanical_read_before_edit import (
    detect_edit_without_read,
)
from agent_memory_lite.enforcement.mechanical_search_before_arch import (
    RULE_TAG as SEARCH_BEFORE_ARCH_TAG,
)
from agent_memory_lite.enforcement.mechanical_search_before_arch import (
    detect_arch_write_without_search,
)
from agent_memory_lite.enforcement.rule_loader import EnforcementRule
from agent_memory_lite.enforcement.verdict import RuleViolation


def _run_magic_number(tool_name: str, tool_input: dict[str, Any], trail: list[str]) -> str | None:
    """Wrap the magic-number detector with tool-input plumbing.

    Edit uses ``new_string``; Write uses ``content``. Both carry the
    target file in ``file_path``. Other tool names short-circuit.
    ``trail`` is unused — this detector is payload-only.
    """
    del trail  # payload-only detector
    if tool_name not in {"Edit", "Write"}:
        return None
    file_path = tool_input.get("file_path") or ""
    text = tool_input.get("new_string") or tool_input.get("content") or ""
    if not isinstance(text, str) or not isinstance(file_path, str):
        return None
    return detect_magic_number(text, file_path)


def _run_decision_provenance(
    tool_name: str, tool_input: dict[str, Any], trail: list[str]
) -> str | None:
    del trail  # payload-only detector
    return detect_decision_without_provenance(tool_name, tool_input)


_Detector = Callable[[str, dict[str, Any], list[str]], str | None]

_DETECTOR_REGISTRY: dict[str, _Detector] = {
    MAGIC_NUMBER_TAG: _run_magic_number,
    DECISION_PROVENANCE_TAG: _run_decision_provenance,
    READ_BEFORE_EDIT_TAG: detect_edit_without_read,
    IMPACT_CHECK_BEFORE_READ_TAG: detect_read_without_impact_check,
    SEARCH_BEFORE_ARCH_TAG: detect_arch_write_without_search,
}


def _resolve_detector(rule: EnforcementRule) -> _Detector | None:
    for tag in rule.applies_to:
        detector = _DETECTOR_REGISTRY.get(tag)
        if detector is not None:
            return detector
    return None


def register_detector(tag: str, detector: _Detector) -> None:
    """Register a mechanical detector under its rule tag.

    Used by trail-aware detector modules to wire themselves into the
    dispatch table without circular imports.
    """
    _DETECTOR_REGISTRY[tag] = detector


def check_mechanical(
    rules: list[EnforcementRule],
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    trail: list[str] | None = None,
) -> list[RuleViolation]:
    """Run every mechanical-tagged rule and collect violations.

    ``trail`` carries the session's prior tool-call names (see
    ``session_trail.read_prior_tool_calls``); trail-aware detectors
    use it to check ordering invariants such as
    memory_impact_check-before-Edit.
    """
    trail_value = trail or []
    violations: list[RuleViolation] = []
    for rule in rules:
        if rule.level != "mechanical":
            continue
        detector = _resolve_detector(rule)
        if detector is None:
            continue
        diagnostic = detector(tool_name, tool_input, trail_value)
        if diagnostic is None:
            continue
        violations.append(
            RuleViolation(
                rule_id=rule.id,
                rule_name=rule.name,
                why=diagnostic,
                enforcement_level="mechanical",
            )
        )
    return violations
