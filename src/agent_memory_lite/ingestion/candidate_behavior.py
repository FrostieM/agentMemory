"""Build behavior payloads from high-trust memory candidates."""

from __future__ import annotations

from typing import Protocol

from agent_memory_lite.models.behavior import BehaviorInstructionIn
from agent_memory_lite.models.enums import (
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    BehaviorInstructionScope,
    MemoryCandidateKind,
)


class CandidateBehaviorSource(Protocol):
    kind: MemoryCandidateKind
    subject: str
    evidence: str
    source_episode_id: str | None
    confidence: float


def behavior_payload_from_candidate(
    candidate: CandidateBehaviorSource,
    *,
    workspace_id: str,
) -> BehaviorInstructionIn:
    label = _label_for_kind(candidate.kind)
    priority = (
        BehaviorInstructionPriority.SYSTEM_BOUND
        if candidate.kind == MemoryCandidateKind.CONSTRAINT
        else BehaviorInstructionPriority.PROJECT_CONVENTION
    )
    return BehaviorInstructionIn(
        workspace_id=workspace_id,
        name=_behavior_name(candidate, label),
        rule=candidate.evidence or candidate.subject,
        kind=BehaviorInstructionKind.OPERATING_RULE,
        scope=BehaviorInstructionScope.WORKSPACE,
        priority=priority,
        rationale=f"Promoted from a high-trust {label} candidate.",
        applies_to=[],
        source_episode_id=candidate.source_episode_id,
        source_type="memory_candidate" if _candidate_id(candidate) else "compaction_candidate",
        source_id=_candidate_id(candidate),
        confidence=candidate.confidence,
        active=True,
    )


def _label_for_kind(kind: MemoryCandidateKind) -> str:
    if kind == MemoryCandidateKind.CONSTRAINT:
        return "constraint"
    return "operating-rule"


def _behavior_name(candidate: CandidateBehaviorSource, label: str) -> str:
    candidate_id = _candidate_id(candidate)
    if candidate_id:
        return f"candidate:{label}:{candidate_id}"
    subject = " ".join(candidate.subject.split()).lower()[:96]
    return f"candidate:{label}:{subject or 'unnamed'}"


def _candidate_id(candidate: CandidateBehaviorSource) -> str | None:
    raw = getattr(candidate, "id", None)
    return raw if isinstance(raw, str) and raw else None
