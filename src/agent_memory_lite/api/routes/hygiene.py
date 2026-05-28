"""GET /memory/hygiene_report."""

from __future__ import annotations

from fastapi import APIRouter, Query

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
    ensure_workspace_writable,
)
from agent_memory_lite.api.schemas.hygiene import (
    HygieneFindingResponse,
    HygieneReportResponse,
    QualityGateFindingResponse,
    QualityGateResponse,
)
from agent_memory_lite.api.workspace_routing import ensure_workspace_matches_db
from agent_memory_lite.maintenance.hygiene import run_hygiene_report
from agent_memory_lite.maintenance.hygiene_persist import persist_findings
from agent_memory_lite.maintenance.quality_gate import run_quality_gate

router = APIRouter()


@router.get("/memory/hygiene_report", response_model=HygieneReportResponse)
def hygiene_report_route(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(default="default"),
    persist: bool = Query(default=False),
) -> HygieneReportResponse:
    ensure_workspace_readable(workspace_id, settings)
    # v1.5: only pass capability_stale_days when the maturity flag is on,
    # so flag-off behavior of /memory/hygiene_report is unchanged.
    # v1.6: same pattern for cold candidates.
    capability_stale_days = (
        settings.capability_stale_days if settings.capability_maturity_enabled else None
    )
    cold_stale_days = settings.cold_stale_days if settings.cold_tracking_enabled else None
    report = run_hygiene_report(
        conn,
        workspace_id=workspace_id,
        capability_stale_days=capability_stale_days,
        cold_stale_days=cold_stale_days,
    )
    # v1.9: optional persistence of findings as recurrence-aware
    # maintenance_events. Off unless both ?persist=true AND the env flag.
    if persist and settings.hygiene_persist_enabled:
        ensure_workspace_writable(workspace_id, settings)
        ensure_workspace_matches_db(conn, workspace_id, settings)
        persist_findings(
            conn,
            workspace_id=workspace_id,
            findings=list(report.findings),
            threshold=settings.recurrence_threshold,
        )
        conn.commit()
    return HygieneReportResponse(
        status=report.status,
        workspace_id=report.workspace_id,
        generated_at=report.generated_at,
        counts=report.counts,
        findings=[
            HygieneFindingResponse(
                kind=finding.kind,
                severity=finding.severity,
                target_type=finding.target_type,
                target_id=finding.target_id,
                summary=finding.summary,
                details=finding.details,
            )
            for finding in report.findings
        ],
    )


@router.get("/memory/quality_gate", response_model=QualityGateResponse)
def quality_gate_route(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(default="default"),
) -> QualityGateResponse:
    ensure_workspace_readable(workspace_id, settings)
    report = run_quality_gate(conn, workspace_id=workspace_id)
    return QualityGateResponse(
        status=report.status,
        workspace_id=report.workspace_id,
        generated_at=report.generated_at,
        counts=report.counts,
        findings=[
            QualityGateFindingResponse(
                kind=finding.kind,
                severity=finding.severity,
                target_type=finding.target_type,
                target_id=finding.target_id,
                summary=finding.summary,
                details=finding.details,
            )
            for finding in report.findings
        ],
    )
