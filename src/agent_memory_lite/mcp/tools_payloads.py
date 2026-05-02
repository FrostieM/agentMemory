"""Shared payload converters + ToolDefinition for MCP tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_memory_lite.utils.text_encoding import repair_common_mojibake

ToolHandler = Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    handler: ToolHandler


def candidate_payload(candidate: Any) -> dict[str, Any]:
    return {
        "candidate_id": candidate.id,
        "workspace_id": candidate.workspace_id,
        "kind": candidate.kind.value,
        "subject": candidate.subject,
        "predicate": candidate.predicate,
        "object": candidate.object,
        "evidence": candidate.evidence,
        "confidence": candidate.confidence,
        "importance": candidate.importance,
        "trust_level": candidate.trust_level.value,
        "source_episode_id": candidate.source_episode_id,
        "status": candidate.status.value,
        "promoted_target_type": candidate.promoted_target_type,
        "promoted_target_id": candidate.promoted_target_id,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
        "decided_at": candidate.decided_at,
    }


def capability_link_payload(link: Any) -> dict[str, Any]:
    return {
        "link_id": link.id,
        "workspace_id": link.workspace_id,
        "target_type": link.target_type.value,
        "target_id": link.target_id,
        "capability_type": link.capability_type.value,
        "capability_id": link.capability_id,
        "capability_name": link.capability_name,
        "relation": link.relation.value,
        "rationale": link.rationale,
        "strength": link.strength,
        "source_episode_id": link.source_episode_id,
        "created_at": link.created_at,
        "updated_at": link.updated_at,
    }


def maintenance_event_payload(event: Any) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "workspace_id": event.workspace_id,
        "kind": event.kind,
        "severity": event.severity.value,
        "status": event.status.value,
        "summary": event.summary,
        "details": event.details,
        "source_episode_id": event.source_episode_id,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "created_at": event.created_at,
        "resolved_at": event.resolved_at,
    }


def behavior_instruction_payload(item: Any) -> dict[str, Any]:
    return {
        "instruction_id": item.id,
        "workspace_id": item.workspace_id,
        "name": item.name,
        "kind": item.kind.value,
        "scope": item.scope.value,
        "priority": item.priority.value,
        "rule": item.rule,
        "rationale": item.rationale,
        "applies_to": item.applies_to,
        "conflict_policy": item.conflict_policy.value,
        "source_episode_id": item.source_episode_id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "reviewed_by": item.reviewed_by,
        "reviewed_at": item.reviewed_at,
        "expires_at": item.expires_at,
        "last_applied_at": item.last_applied_at,
        "application_count": item.application_count,
        "conflict_group": item.conflict_group,
        "confidence": item.confidence,
        "active": item.active,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def decision_payload(item: Any) -> dict[str, Any]:
    return {
        "decision_id": item.id,
        "title": repair_common_mojibake(item.title),
        "decision_text": repair_common_mojibake(item.decision_text),
        "rationale": repair_common_mojibake(item.rationale) if item.rationale else None,
        "status": item.status.value,
        "supersedes_decision_id": item.supersedes_decision_id,
        "source_episode_id": item.source_episode_id,
        "confidence": item.confidence,
        "importance": item.importance,
        "valid_from": item.valid_from,
        "valid_to": item.valid_to,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
