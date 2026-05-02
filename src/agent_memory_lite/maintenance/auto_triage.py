"""Safe automatic triage for memory hygiene findings.

Hygiene reports intentionally do not mutate memory. This module provides an
explicit automation layer for gaps that can be closed with bounded risk, such
as adding reviewable capability links suggested by hygiene.

Result dataclasses live in ``auto_triage_models.py``; coercion +
suggestion helpers live in ``auto_triage_helpers.py``. They are
re-exported here so existing callers keep working.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.capability_link_writer import link_capability
from agent_memory_lite.maintenance.auto_triage_helpers import (
    applied_from_suggestion,
    best_suggestion_stats,
    candidate_payload,
    passes_thresholds,
    suggestions_for,
)
from agent_memory_lite.maintenance.auto_triage_models import (
    AppliedCapabilityLink,
    AutoTriageResult,
    SkippedCapabilityFinding,
)
from agent_memory_lite.maintenance.hygiene import run_hygiene_report

__all__ = [
    "AppliedCapabilityLink",
    "AutoTriageResult",
    "SkippedCapabilityFinding",
    "triage_capability_links",
]


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
        suggestions = suggestions_for(finding)
        accepted = [
            suggestion
            for suggestion in suggestions
            if passes_thresholds(
                suggestion,
                min_strength=min_strength,
                min_match_score=min_match_score,
            )
        ][:max_links_per_target]
        if not accepted:
            best_strength, best_match_score = best_suggestion_stats(suggestions)
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
                link = link_capability(conn, candidate_payload(suggestion))
                link_id = link.id
            applied.append(applied_from_suggestion(suggestion, link_id=link_id))

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
