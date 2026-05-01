"""GET /memory/hygiene_report."""

from __future__ import annotations

from fastapi import APIRouter, Query

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.schemas.hygiene import (
    HygieneFindingResponse,
    HygieneReportResponse,
    QualityGateFindingResponse,
    QualityGateResponse,
)
from agent_memory_lite.maintenance.hygiene import run_hygiene_report
from agent_memory_lite.maintenance.quality_gate import run_quality_gate

router = APIRouter()


@router.get("/memory/hygiene_report", response_model=HygieneReportResponse)
def hygiene_report_route(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(default="default"),
) -> HygieneReportResponse:
    ensure_workspace_readable(workspace_id, settings)
    report = run_hygiene_report(conn, workspace_id=workspace_id)
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
