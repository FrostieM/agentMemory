"""Result types for the auto-triage pass.

Split out of ``auto_triage.py`` so the orchestrator stays under the
SLOC ceiling. The dataclasses are reusable by tools that read the
result (CLI scripts, dashboard).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AppliedCapabilityLink:
    target_type: str
    target_id: str
    capability_type: str
    capability_id: str
    capability_name: str
    relation: str
    strength: float
    match_score: float
    link_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_type": self.target_type,
            "target_id": self.target_id,
            "capability_type": self.capability_type,
            "capability_id": self.capability_id,
            "capability_name": self.capability_name,
            "relation": self.relation,
            "strength": self.strength,
            "match_score": self.match_score,
            "link_id": self.link_id,
        }


@dataclass(frozen=True, slots=True)
class SkippedCapabilityFinding:
    target_type: str
    target_id: str
    reason: str
    best_strength: float | None = None
    best_match_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_type": self.target_type,
            "target_id": self.target_id,
            "reason": self.reason,
            "best_strength": self.best_strength,
            "best_match_score": self.best_match_score,
        }


@dataclass(frozen=True, slots=True)
class AutoTriageResult:
    workspace_id: str
    dry_run: bool
    before_counts: dict[str, int]
    after_counts: dict[str, int]
    applied_links: list[AppliedCapabilityLink] = field(default_factory=list)
    skipped_findings: list[SkippedCapabilityFinding] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.after_counts.get("total_findings", 0) == 0:
            return "ok"
        if self.applied_links:
            return "improved"
        return "unchanged"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_id": self.workspace_id,
            "dry_run": self.dry_run,
            "before_counts": self.before_counts,
            "after_counts": self.after_counts,
            "applied_links": [item.to_dict() for item in self.applied_links],
            "skipped_findings": [item.to_dict() for item in self.skipped_findings],
        }
