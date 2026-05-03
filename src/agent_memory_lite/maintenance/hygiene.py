"""Detailed memory hygiene report.

Integrity checks answer whether retrieval is mechanically consistent. Hygiene
checks answer whether the memory remains scientifically useful: open candidates
must be triaged, theories must be testable, experiments must close, insights
must be linked, and important objects should be influenced by roles/skills.

Implementation:
- ``hygiene_models.py`` — result types + small helpers
- ``hygiene_candidate_check.py`` — stale candidate finder
- ``hygiene_theory_check.py`` — theory validation/evidence gaps
- ``hygiene_experiment_check.py`` — overdue/stale/no-criteria experiments
- ``hygiene_insight_decision_check.py`` — insight + decision gaps
- ``hygiene_capabilities.py`` — active-capability loader
- ``hygiene_capability_ranker.py`` — suggestion ranker
- ``hygiene_capability_links.py`` — find unlinked targets
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.maintenance.cold_scanner import find_cold_candidates
from agent_memory_lite.maintenance.hygiene_capability_links import (
    find_unlinked_capability_targets,
)
from agent_memory_lite.maintenance.hygiene_capability_maturity import (
    find_stale_capabilities,
)
from agent_memory_lite.maintenance.hygiene_models import HygieneFinding, HygieneReport
from agent_memory_lite.maintenance.hygiene_simple_checks import (
    find_decision_gaps,
    find_experiment_gaps,
    find_insight_gaps,
    find_stale_candidates,
    find_theory_gaps,
)
from agent_memory_lite.utils.time import iso_now

__all__ = ["HygieneFinding", "HygieneReport", "run_hygiene_report"]


def _count_by_kind(findings: list[HygieneFinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
    return counts


def run_hygiene_report(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    candidate_stale_days: int = 14,
    experiment_stale_days: int = 30,
    importance_threshold: float = 0.8,
    capability_stale_days: int | None = None,
    cold_stale_days: int | None = None,
) -> HygieneReport:
    findings: list[HygieneFinding] = []
    findings.extend(
        find_stale_candidates(conn, workspace_id=workspace_id, stale_days=candidate_stale_days)
    )
    findings.extend(find_theory_gaps(conn, workspace_id=workspace_id))
    findings.extend(
        find_experiment_gaps(
            conn,
            workspace_id=workspace_id,
            stale_days=experiment_stale_days,
        )
    )
    findings.extend(find_insight_gaps(conn, workspace_id=workspace_id))
    findings.extend(find_decision_gaps(conn, workspace_id=workspace_id))
    findings.extend(
        find_unlinked_capability_targets(
            conn,
            workspace_id=workspace_id,
            importance_threshold=importance_threshold,
        )
    )
    if capability_stale_days is not None and capability_stale_days > 0:
        findings.extend(
            find_stale_capabilities(
                conn, workspace_id=workspace_id, stale_days=capability_stale_days
            )
        )
    if cold_stale_days is not None and cold_stale_days > 0:
        for candidate in find_cold_candidates(
            conn, workspace_id=workspace_id, older_than_days=cold_stale_days
        ):
            findings.append(
                HygieneFinding(
                    kind="cold_candidate",
                    severity="info",
                    target_type=candidate.kind,
                    target_id=candidate.id,
                    summary=(
                        f"{candidate.kind} not retrieved in over {cold_stale_days} "
                        "days; consider archiving or pinning."
                    ),
                    details={"last_retrieved_at": candidate.last_retrieved_at},
                )
            )
    counts = _count_by_kind(findings)
    counts["total_findings"] = len(findings)
    status = "warning" if findings else "ok"
    return HygieneReport(
        status=status,
        workspace_id=workspace_id,
        generated_at=iso_now(),
        counts=counts,
        findings=findings,
    )
