"""Capability suggestion ranker for hygiene findings."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.maintenance.hygiene_capabilities import CapabilityCandidate
from agent_memory_lite.maintenance.hygiene_models import tokens_from

_RELATION_MAP = {
    ("theory", "role"): "critique_lens",
    ("theory", "skill"): "evidence_method",
    ("theory", "playbook"): "validation_playbook",
    ("experiment", "role"): "reviewer",
    ("experiment", "skill"): "required_skill",
    ("experiment", "playbook"): "validation_playbook",
    ("decision", "role"): "implementation_role",
    ("decision", "skill"): "method",
    ("decision", "playbook"): "validation_playbook",
}


_BONUS_MAP = {
    ("theory", "role"): 0.15,
    ("theory", "skill"): 0.25,
    ("theory", "playbook"): 0.25,
    ("experiment", "role"): 0.15,
    ("experiment", "skill"): 0.25,
    ("experiment", "playbook"): 0.30,
    ("decision", "role"): 0.35,
    ("decision", "skill"): 0.0,
    ("decision", "playbook"): 0.25,
}


def suggested_relation(target_type: str, capability_type: str) -> str:
    return _RELATION_MAP.get((target_type, capability_type), "method")


def capability_type_bonus(target_type: str, capability_type: str) -> float:
    return _BONUS_MAP.get((target_type, capability_type), 0.0)


def suggest_capability_links(
    capabilities: list[CapabilityCandidate],
    *,
    workspace_id: str,
    target_type: str,
    target_id: str,
    target_label: str,
    target_text: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    target_terms = tokens_from(f"{target_label} {target_text}")
    capability_terms: list[tuple[CapabilityCandidate, set[str]]] = [
        (capability, tokens_from(capability.text)) for capability in capabilities
    ]
    term_counts: dict[str, int] = {}
    for _, terms in capability_terms:
        for term in terms:
            term_counts[term] = term_counts.get(term, 0) + 1

    ranked: list[tuple[float, float, str, CapabilityCandidate, list[str]]] = []
    match_scores: dict[str, float] = {}
    common_term_limit = max(2, len(capability_terms) // 3)
    for capability, terms in capability_terms:
        matched_terms = target_terms & terms
        if not matched_terms:
            continue
        rare_matches = [term for term in matched_terms if term_counts[term] <= common_term_limit]
        weighted_match_score = sum(1.0 / term_counts[term] for term in matched_terms)
        if not rare_matches and weighted_match_score < 1.2:
            continue
        ordered_terms = sorted(
            matched_terms,
            key=lambda term: (1.0 / term_counts[term], len(term), term),
            reverse=True,
        )
        score = (
            weighted_match_score
            + capability.confidence
            + capability_type_bonus(target_type, capability.capability_type)
        )
        match_scores[capability.capability_id] = weighted_match_score
        ranked.append(
            (score, capability.confidence, capability.capability_name, capability, ordered_terms)
        )
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    suggestions: list[dict[str, Any]] = []
    for _, confidence, _name, capability, ordered_matches in ranked[:limit]:
        relation = suggested_relation(target_type, capability.capability_type)
        weighted_score = match_scores[capability.capability_id]
        strength = min(0.9, round(0.45 + 0.06 * weighted_score + 0.10 * confidence, 2))
        matched_preview = ordered_matches[:8]
        suggestions.append(
            {
                "workspace_id": workspace_id,
                "target_type": target_type,
                "target_id": target_id,
                "capability_type": capability.capability_type,
                "capability_id": capability.capability_id,
                "capability_name": capability.capability_name,
                "relation": relation,
                "strength": strength,
                "match_score": round(weighted_score, 3),
                "matched_terms": matched_preview,
                "rationale": (
                    "Suggested by hygiene because the target and capability share terms: "
                    f"{', '.join(matched_preview)}."
                ),
            }
        )
    return suggestions
