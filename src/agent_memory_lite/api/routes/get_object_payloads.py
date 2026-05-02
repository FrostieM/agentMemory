"""Payload renderers + dispatch table for /memory/get_object.

Split out of ``get_object.py`` so the route handler stays small and the
payload shapes are easy to tweak without touching FastAPI plumbing.

A ``kind`` (decision / theory / snapshot / etc.) maps to a tuple of:

* ``fetcher`` — repository function that returns the model or ``None``;
* ``render`` — function that turns the model into the wire-shape dict.

The agent extends the surface by adding one entry instead of another
``elif`` branch in the route handler.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.repositories.behavior_repo import get_behavior_instruction_by_id
from agent_memory_lite.repositories.capabilities_repo import (
    get_playbook_by_id,
    get_role_by_id,
    get_skill_by_id,
)
from agent_memory_lite.repositories.decisions_repo import get_decision
from agent_memory_lite.repositories.research_repo import (
    get_concept_by_id,
    get_experiment,
    get_insight,
    get_snapshot,
)


def decision_payload(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "workspace_id": item.workspace_id,
        "title": item.title,
        "decision_text": item.decision_text,
        "rationale": item.rationale,
        "status": item.status.value,
        "supersedes_decision_id": item.supersedes_decision_id,
        "valid_from": item.valid_from,
        "valid_to": item.valid_to,
        "confidence": item.confidence,
        "importance": item.importance,
        "source_episode_id": item.source_episode_id,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def theory_payload(item: Any, evidence: list[Any]) -> dict[str, Any]:
    return {
        "id": item.id,
        "workspace_id": item.workspace_id,
        "title": item.title,
        "domain": item.domain,
        "claim": item.claim,
        "mechanism": item.mechanism,
        "predictions": item.predictions,
        "validation_criteria": item.validation_criteria,
        "experiment_plan": item.experiment_plan,
        "tags": item.tags,
        "dependent_decision_ids": item.dependent_decision_ids,
        "status": item.status.value,
        "confidence": item.confidence,
        "importance": item.importance,
        "evidence_count": item.evidence_count,
        "evidence_strength": item.evidence_strength,
        "source_episode_id": item.source_episode_id,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "evidence": [
            {
                "id": ev.id,
                "kind": ev.kind.value,
                "summary": ev.summary,
                "metrics": ev.metrics,
                "artifact_path": ev.artifact_path,
                "confidence": ev.confidence,
                "observed_at": ev.observed_at,
                "source_episode_id": ev.source_episode_id,
            }
            for ev in evidence
        ],
    }


def generic_payload(item: Any) -> dict[str, Any]:
    return dict(item.model_dump(mode="json"))


# Dispatch table: kind → (fetcher, payload_renderer). Theory is special
# because the payload depends on whether evidence was requested, so the
# route handler keeps its own branch for that one.
KIND_FETCHERS: dict[str, tuple[Any, Any]] = {
    "decision": (get_decision, decision_payload),
    "snapshot": (get_snapshot, generic_payload),
    "experiment": (get_experiment, generic_payload),
    "insight": (get_insight, generic_payload),
    "concept": (get_concept_by_id, generic_payload),
    "role": (get_role_by_id, generic_payload),
    "skill": (get_skill_by_id, generic_payload),
    "playbook": (get_playbook_by_id, generic_payload),
    "behavior_instruction": (get_behavior_instruction_by_id, generic_payload),
}
