"""Coercion + suggestion helpers for the auto-triage pass.

Split out of ``auto_triage.py`` so the orchestrator stays under the
SLOC ceiling.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.maintenance.auto_triage_models import AppliedCapabilityLink
from agent_memory_lite.maintenance.hygiene import HygieneFinding
from agent_memory_lite.models.capability_links import CapabilityLinkIn
from agent_memory_lite.models.enums import (
    CapabilityLinkRelation,
    CapabilityLinkTargetType,
    CapabilityType,
)


def as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def as_str(value: object) -> str:
    return str(value) if value is not None else ""


def suggestions_for(finding: HygieneFinding) -> list[dict[str, Any]]:
    raw = finding.details.get("suggested_capability_links", [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def passes_thresholds(
    suggestion: dict[str, Any],
    *,
    min_strength: float,
    min_match_score: float,
) -> bool:
    return (
        as_float(suggestion.get("strength")) >= min_strength
        and as_float(suggestion.get("match_score")) >= min_match_score
    )


def candidate_payload(suggestion: dict[str, Any]) -> CapabilityLinkIn:
    target_type = CapabilityLinkTargetType(as_str(suggestion.get("target_type")))
    capability_type = CapabilityType(as_str(suggestion.get("capability_type")))
    relation = CapabilityLinkRelation(as_str(suggestion.get("relation")))
    matched_terms = suggestion.get("matched_terms", [])
    matched_text = (
        ", ".join(str(item) for item in matched_terms) if isinstance(matched_terms, list) else ""
    )
    rationale = (
        "Auto-triage accepted hygiene capability suggestion"
        f" (match_score={as_float(suggestion.get('match_score')):.3f}, "
        f"strength={as_float(suggestion.get('strength')):.2f}, "
        f"matched_terms={matched_text})."
    )
    return CapabilityLinkIn(
        workspace_id=as_str(suggestion.get("workspace_id")),
        target_type=target_type,
        target_id=as_str(suggestion.get("target_id")),
        capability_type=capability_type,
        capability_id=as_str(suggestion.get("capability_id")),
        relation=relation,
        rationale=rationale,
        strength=as_float(suggestion.get("strength")),
    )


def applied_from_suggestion(
    suggestion: dict[str, Any], *, link_id: str | None
) -> AppliedCapabilityLink:
    return AppliedCapabilityLink(
        target_type=as_str(suggestion.get("target_type")),
        target_id=as_str(suggestion.get("target_id")),
        capability_type=as_str(suggestion.get("capability_type")),
        capability_id=as_str(suggestion.get("capability_id")),
        capability_name=as_str(suggestion.get("capability_name")),
        relation=as_str(suggestion.get("relation")),
        strength=as_float(suggestion.get("strength")),
        match_score=as_float(suggestion.get("match_score")),
        link_id=link_id,
    )


def best_suggestion_stats(
    suggestions: list[dict[str, Any]],
) -> tuple[float | None, float | None]:
    if not suggestions:
        return None, None
    best = max(
        suggestions,
        key=lambda item: (as_float(item.get("strength")), as_float(item.get("match_score"))),
    )
    return as_float(best.get("strength")), as_float(best.get("match_score"))
