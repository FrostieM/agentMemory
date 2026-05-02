"""Wire-shape dataclasses for the explain-context surface.

Split out of ``explain.py`` so the orchestrator stays under the SLOC
ceiling. Holds ``ContextExplanation`` plus its source/scored/used/
suppressed sub-records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceCandidateExplanation:
    id: str
    source: str
    rank: int
    raw_score: float
    path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScoredCandidateExplanation:
    id: str
    score: float
    sources: list[str]
    included: bool
    reason: str
    path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SuppressedBehaviorExplanation:
    id: str
    name: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UsedContextObjectExplanation:
    table: str
    id: str
    label: str
    relation: str
    updated_at: str | None = None
    rank: int = 0


@dataclass(frozen=True, slots=True)
class ContextExplanation:
    workspace_id: str
    query: str
    max_tokens: int
    context_tokens: int
    section_counts: dict[str, int]
    source_candidates: list[SourceCandidateExplanation]
    scored_candidates: list[ScoredCandidateExplanation]
    included_ids: list[str]
    used_context_objects: list[UsedContextObjectExplanation] = field(default_factory=list)
    suppressed_behavior_instructions: list[SuppressedBehaviorExplanation] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "query": self.query,
            "max_tokens": self.max_tokens,
            "context_tokens": self.context_tokens,
            "section_counts": self.section_counts,
            "source_candidates": [
                {
                    "id": item.id,
                    "source": item.source,
                    "rank": item.rank,
                    "raw_score": item.raw_score,
                    "path": item.path,
                    "metadata": item.metadata,
                }
                for item in self.source_candidates
            ],
            "scored_candidates": [
                {
                    "id": item.id,
                    "score": item.score,
                    "sources": item.sources,
                    "included": item.included,
                    "reason": item.reason,
                    "path": item.path,
                    "metadata": item.metadata,
                }
                for item in self.scored_candidates
            ],
            "included_ids": self.included_ids,
            "used_context_objects": [
                {
                    "table": item.table,
                    "id": item.id,
                    "label": item.label,
                    "relation": item.relation,
                    "updated_at": item.updated_at,
                    "rank": item.rank,
                }
                for item in self.used_context_objects
            ],
            "suppressed_behavior_instructions": [
                {
                    "id": item.id,
                    "name": item.name,
                    "reason": item.reason,
                    "details": item.details,
                }
                for item in self.suppressed_behavior_instructions
            ],
        }
