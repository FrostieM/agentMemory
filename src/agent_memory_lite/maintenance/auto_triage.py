"""Safe automatic triage for memory hygiene findings.

Hygiene reports intentionally do not mutate memory. This module provides an
explicit automation layer for gaps that can be closed with bounded risk, such
as adding reviewable capability links suggested by hygiene.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from agent_memory_lite.ingestion.capability_link_writer import link_capability
from agent_memory_lite.maintenance.hygiene import HygieneFinding, run_hygiene_report
from agent_memory_lite.models.capability_links import CapabilityLinkIn
from agent_memory_lite.models.enums import (
    CapabilityLinkRelation,
    CapabilityLinkTargetType,
    CapabilityType,
)


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


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _as_str(value: object) -> str:
    return str(value) if value is not None else ""


def _suggestions(finding: HygieneFinding) -> list[dict[str, Any]]:
    raw = finding.details.get("suggested_capability_links", [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _passes_thresholds(
    suggestion: dict[str, Any],
    *,
    min_strength: float,
    min_match_score: float,
) -> bool:
    return (
        _as_float(suggestion.get("strength")) >= min_strength
        and _as_float(suggestion.get("match_score")) >= min_match_score
    )


def _candidate_payload(suggestion: dict[str, Any]) -> CapabilityLinkIn:
    target_type = CapabilityLinkTargetType(_as_str(suggestion.get("target_type")))
    capability_type = CapabilityType(_as_str(suggestion.get("capability_type")))
    relation = CapabilityLinkRelation(_as_str(suggestion.get("relation")))
    matched_terms = suggestion.get("matched_terms", [])
    matched_text = (
        ", ".join(str(item) for item in matched_terms) if isinstance(matched_terms, list) else ""
    )
    rationale = (
        "Auto-triage accepted hygiene capability suggestion"
        f" (match_score={_as_float(suggestion.get('match_score')):.3f}, "
        f"strength={_as_float(suggestion.get('strength')):.2f}, "
        f"matched_terms={matched_text})."
    )
    return CapabilityLinkIn(
        workspace_id=_as_str(suggestion.get("workspace_id")),
        target_type=target_type,
        target_id=_as_str(suggestion.get("target_id")),
        capability_type=capability_type,
        capability_id=_as_str(suggestion.get("capability_id")),
        relation=relation,
        rationale=rationale,
        strength=_as_float(suggestion.get("strength")),
    )


def _applied_from_suggestion(
    suggestion: dict[str, Any],
    *,
    link_id: str | None,
) -> AppliedCapabilityLink:
    return AppliedCapabilityLink(
        target_type=_as_str(suggestion.get("target_type")),
        target_id=_as_str(suggestion.get("target_id")),
        capability_type=_as_str(suggestion.get("capability_type")),
        capability_id=_as_str(suggestion.get("capability_id")),
        capability_name=_as_str(suggestion.get("capability_name")),
        relation=_as_str(suggestion.get("relation")),
        strength=_as_float(suggestion.get("strength")),
        match_score=_as_float(suggestion.get("match_score")),
        link_id=link_id,
    )


def _best_suggestion_stats(
    suggestions: list[dict[str, Any]],
) -> tuple[float | None, float | None]:
    if not suggestions:
        return None, None
    best = max(
        suggestions,
        key=lambda item: (_as_float(item.get("strength")), _as_float(item.get("match_score"))),
    )
    return _as_float(best.get("strength")), _as_float(best.get("match_score"))


def triage_capability_links(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    apply: bool = False,
    min_strength: float = 0.6,
    min_match_score: float = 1.0,
    max_links_per_target: int = 1,
    importance_threshold: float = 0.8,
) -> AutoTriageResult:
    """Apply safe capability-link suggestions from a hygiene report.

    The function only handles ``missing_capability_link`` findings. It does not
    resolve weak theories, stale experiments, or candidate review queues because
    those require semantic judgment.
    """

    before = run_hygiene_report(
        conn,
        workspace_id=workspace_id,
        importance_threshold=importance_threshold,
    )
    applied: list[AppliedCapabilityLink] = []
    skipped: list[SkippedCapabilityFinding] = []
    for finding in before.findings:
        if finding.kind != "missing_capability_link":
            continue
        suggestions = _suggestions(finding)
        accepted = [
            suggestion
            for suggestion in suggestions
            if _passes_thresholds(
                suggestion,
                min_strength=min_strength,
                min_match_score=min_match_score,
            )
        ][:max_links_per_target]
        if not accepted:
            best_strength, best_match_score = _best_suggestion_stats(suggestions)
            skipped.append(
                SkippedCapabilityFinding(
                    target_type=finding.target_type,
                    target_id=finding.target_id,
                    reason="no suggestion passed thresholds",
                    best_strength=best_strength,
                    best_match_score=best_match_score,
                )
            )
            continue
        for suggestion in accepted:
            link_id: str | None = None
            if apply:
                link = link_capability(conn, _candidate_payload(suggestion))
                link_id = link.id
            applied.append(_applied_from_suggestion(suggestion, link_id=link_id))

    after = (
        run_hygiene_report(
            conn,
            workspace_id=workspace_id,
            importance_threshold=importance_threshold,
        )
        if apply
        else before
    )
    return AutoTriageResult(
        workspace_id=workspace_id,
        dry_run=not apply,
        before_counts=before.counts,
        after_counts=after.counts,
        applied_links=applied,
        skipped_findings=skipped,
    )
